"""Chat agent: OpenRouter LLM + tools over the scraped data (hybrid RAG)."""
import json
import os
import time

import httpx

from app.data_store import DataStore
from app.retriever import Retriever

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Free models, tried in order (OpenRouter falls back automatically on errors/rate limits).
MODELS = [m.strip() for m in os.getenv(
    "OPENROUTER_MODELS",
    "nvidia/nemotron-3-ultra-550b-a55b:free,nvidia/nemotron-3.5-super-vl:free,openrouter/free",
).split(",") if m.strip()]
MAX_TOOL_ROUNDS = 4
CACHE_TTL = 24 * 3600  # seconds

SYSTEM_PROMPT = """You are "Estate Assistant", a real-estate chatbot built on data scraped in November 2026 from two public websites:
1. DarGlobal (darglobal.co.uk) – an international luxury developer. {dg} projects across {countries}, plus company, partner-brand and press pages. DarGlobal does NOT publish prices online: say "price on request" and suggest contacting DarGlobal.
2. Wasalt (wasalt.sa) – a Saudi property marketplace. {wm:,} active listings from its public sitemap (type, size, bedrooms, facing, street width – no prices/cities) for market statistics, plus {wd} fully detailed listing(s) with price and location.

How to answer:
- Use the tools to look things up; never invent projects, prices, numbers or facts. If the data doesn't contain the answer, say so.
- search_listings: filter projects/listings (country, city, type, bedrooms, price, area, keyword).
- market_stats: counts/size statistics across all Wasalt listings (e.g. "how many villas for sale over 500 sqm").
- semantic_search: descriptive questions (amenities, brands, investment benefits, golden visa, about DarGlobal, press news).
- get_listing: full details of one project/listing by id.
- Be concise and helpful. Use short paragraphs or bullet lists. Mention prices with currency.
- Always cite sources as markdown links, e.g. [The Astera](https://darglobal.co.uk/the-astera). Never use bare URLs or 【】 brackets.
- Answer in the user's language (English or Arabic). In Arabic, write clean Modern Standard Arabic and keep project/brand names in their original English form."""

TOOLS = [
    {"type": "function", "function": {
        "name": "search_listings",
        "description": "Filter DarGlobal projects and detailed Wasalt listings by structured criteria.",
        "parameters": {"type": "object", "properties": {
            "source": {"type": "string", "enum": ["darglobal", "wasalt"]},
            "country": {"type": "string", "description": "e.g. UAE, Oman, Saudi Arabia, UK, Spain, Qatar"},
            "city": {"type": "string", "description": "city or district, e.g. Dubai, Riyadh, Muscat"},
            "listing_type": {"type": "string", "description": "sale, rent, or off-plan"},
            "property_type": {"type": "string", "description": "e.g. apartment, villa, land"},
            "min_bedrooms": {"type": "integer"}, "max_bedrooms": {"type": "integer"},
            "min_price": {"type": "number"}, "max_price": {"type": "number"},
            "min_area_sqm": {"type": "number"}, "max_area_sqm": {"type": "number"},
            "keyword": {"type": "string", "description": "words that must appear, e.g. 'golf', 'Trump', 'beach'"},
            "sort_by": {"type": "string", "enum": ["price_asc", "price_desc", "newest"]},
            "limit": {"type": "integer", "description": "max results, default 8"},
        }}}},
    {"type": "function", "function": {
        "name": "market_stats",
        "description": "Counts and area statistics across ~60,000 Wasalt (Saudi) listings. No prices.",
        "parameters": {"type": "object", "properties": {
            "listing_type": {"type": "string", "enum": ["sale", "rent"]},
            "property_type": {"type": "string",
                              "description": "apartment, villa, land, floor, building, rest house, room"},
            "min_area_sqm": {"type": "number"}, "max_area_sqm": {"type": "number"},
            "bedrooms": {"type": "integer"},
            "facing": {"type": "string", "description": "north, south, east, west, north east, ..."},
            "group_by": {"type": "string", "enum": ["listing_type", "property_type", "bedrooms", "facing"]},
        }}}},
    {"type": "function", "function": {
        "name": "semantic_search",
        "description": "Search descriptions and DarGlobal pages by meaning. Returns text passages with URLs.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "source": {"type": "string", "enum": ["darglobal", "wasalt"]},
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_listing",
        "description": "Full details of one project/listing by its id (from search results).",
        "parameters": {"type": "object", "properties": {"listing_id": {"type": "string"}},
                       "required": ["listing_id"]}}},
]


class Agent:
    def __init__(self):
        self._cache = {}
        self.store = DataStore()
        self.retriever = Retriever()
        ov = self.store.overview()
        self.system = SYSTEM_PROMPT.format(
            dg=ov["darglobal_projects"], countries=", ".join(ov["darglobal_countries"]),
            wm=ov["wasalt_market_listings"], wd=ov["wasalt_detailed_listings"])

    # ---- tools ----
    def run_tool(self, name, args):
        try:
            if name == "search_listings":
                return self.store.search_listings(**args)
            if name == "market_stats":
                return self.store.market_stats(**args)
            if name == "semantic_search":
                return self.retriever.search(args["query"], k=6, source=args.get("source"))
            if name == "get_listing":
                return self.store.get_listing(args["listing_id"])
            return {"error": f"unknown tool {name}"}
        except TypeError as e:  # model passed an unexpected argument
            return {"error": f"bad arguments: {e}"}

    # ---- LLM ----
    def _call(self, messages, tools=True):
        """Call OpenRouter; if a model returns an empty reply (e.g. a reasoning model that
        spent its budget thinking), retry starting from the next model in the list."""
        last = None
        for start in range(len(MODELS)):
            msg, model = self._call_once(messages, tools, MODELS[start:])
            if (msg.get("content") or "").strip() or msg.get("tool_calls"):
                return msg, model
            last = (msg, model)
        return last

    def _call_once(self, messages, tools, models):
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        body = {"model": models[0], "models": models, "messages": messages,
                "temperature": 0.2, "max_tokens": 4000,
                "reasoning": {"effort": "low"}}  # keep thinking short on reasoning models
        if tools:
            body["tools"] = TOOLS
        r = httpx.post(OPENROUTER_URL, json=body, timeout=90, headers={
            "Authorization": f"Bearer {key}",
            "X-Title": "Real Estate Chatbot",
        })
        r.raise_for_status()
        data = r.json()
        if "choices" not in data:
            raise RuntimeError(f"OpenRouter error: {data.get('error')}")
        return data["choices"][0]["message"], data.get("model")

    def chat(self, history):
        """Answer with a cache for first-turn questions (saves free-tier quota:
        reviewers often click the same suggested questions)."""
        key = " ".join(history[-1]["content"].lower().split()) if len(history) == 1 else None
        hit = self._cache.get(key) if key else None
        if hit and time.time() - hit[0] < CACHE_TTL:
            return {**hit[1], "cached": True}
        result = self._chat(history)
        if key and not result["answer"].startswith("Sorry"):
            if len(self._cache) >= 300:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (time.time(), result)
        return result

    def _chat(self, history):
        """history: [{role: user|assistant, content: str}, ...] -> {answer, sources, model}"""
        messages = [{"role": "system", "content": self.system}] + history[-10:]
        sources, model = {}, None
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                msg, model = self._call(messages)
                calls = msg.get("tool_calls") or []
                if not calls:
                    return self._result(msg.get("content"), sources, model)
                messages.append({"role": "assistant", "content": msg.get("content") or "",
                                 "tool_calls": calls})
                for c in calls:
                    try:
                        args = json.loads(c["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = self.run_tool(c["function"]["name"], args)
                    self._collect_sources(result, sources)
                    messages.append({"role": "tool", "tool_call_id": c["id"],
                                     "content": json.dumps(result, ensure_ascii=False)[:12000]})
            msg, model = self._call(messages, tools=False)  # force a final answer
            return self._result(msg.get("content"), sources, model)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (400, 404, 422):  # model rejected tool format
                return self._fallback(history)
            raise

    def _fallback(self, history):
        """Plain RAG without tool calling."""
        q = history[-1]["content"]
        hits = self.retriever.search(q, k=6)
        ctx = "\n\n".join(f"[{h['title']}]({h['url']})\n{h['text']}" for h in hits)
        messages = [{"role": "system", "content": self.system + "\n\nContext:\n" + ctx}] + history[-6:]
        msg, model = self._call(messages, tools=False)
        sources = {}
        self._collect_sources(hits, sources)
        return self._result(msg.get("content"), sources, model)

    @staticmethod
    def _collect_sources(result, sources):
        if isinstance(result, dict) and "dataset" in result:  # market_stats
            sources.setdefault("https://wasalt.sa/en", "Wasalt.sa (public sitemap data)")
            return
        items = result if isinstance(result, list) else (
            result.get("results") or ([result] if result.get("url") else []))
        for it in items:
            if isinstance(it, dict) and it.get("url"):
                sources.setdefault(it["url"], it.get("title") or it["url"])

    @staticmethod
    def _result(content, sources, model):
        return {"answer": (content or "Sorry, I couldn't generate an answer.").strip(),
                "sources": [{"url": u, "title": t} for u, t in list(sources.items())[:8]],
                "model": model}
