# Estate Assistant: real-estate AI chatbot (DarGlobal + Wasalt)

An AI chatbot that answers questions about **DarGlobal** luxury projects and the **Wasalt** Saudi property market. It uses data scraped from their public websites, a free LLM through **OpenRouter**, and runs in **Docker**.

**Live demo:** https://estate-assistant.onrender.com  
*(Free hosting: if the app has been idle, the first load can take ~30–50 s to wake up.)*

## What it can answer

- *"Which DarGlobal projects are in Oman and when do they complete?"*
- *"Show me villas with 4+ bedrooms in Spain or Oman"*
- *"What are the investment benefits of buying in Ras Al Khaimah?"*
- *"Who is DarGlobal and which brands do they partner with?"*
- *"How many villas are for sale on Wasalt and what is the typical size?"*
- Arabic questions work too: *"ما هي مشاريع دار جلوبال في السعودية؟"*

Every answer links back to the original source pages.

## Data collected (snapshot: November 2026)

| Source | What | Records |
|---|---|---|
| DarGlobal (darglobal.co.uk) | All projects: title, country/city, type, bedrooms, size range, status, completion date, unit types, amenities, full description | 38 projects in 6 countries |
| DarGlobal | Company, why-invest, FAQ, investor, partner-brand and press pages | 37 pages |
| Wasalt (wasalt.sa) | Every active sale/rent listing in Wasalt's public sitemap: type, area, bedrooms, facing, street width | 60,000 listings |
| Wasalt | Full listing-page detail (price, city, district, features) | sample, see note |

**How it was scraped**
- **DarGlobal** sits behind Imperva bot protection. It was scraped with Playwright: one ordinary browser session, 3–6 s between pages, and only pages robots.txt allows. The site embeds structured Next.js JSON, which was parsed along with the visible text.
- **Wasalt** listing URLs come from the sitemap linked in its robots.txt. Each URL encodes the property type, size, bedrooms, facing and street width.
- **Wasalt detail pages:** a slow, visible-browser run (10–15 s per page) was tried. Cloudflare repeatedly asked for human verification, so detail scraping was **deliberately stopped** rather than bypassing the site's protection. The full-page parser is implemented and was validated on a sample listing.
- **Privacy:** no personal data is stored. Agent and owner names, phone numbers and licence holders are dropped at scrape time.
- **Cleaning:** URLs can't contain ".", so "296.09 sqm" appears as `29609-sqm`. Residential units over 10,000 sqm are rescaled, and implausible bedroom counts are removed.

## Architecture

```
scraper/ (run locally, once)                 app/ (deployed in Docker)
  darglobal.py   --Playwright-->  raw JSONL     data_store.py  structured filters + market stats
  wasalt_sitemap.py  --sitemap--> raw JSONL     retriever.py   semantic search (fastembed, bge-small, numpy)
  normalize.py   --> data/processed/*.jsonl     agent.py       OpenRouter LLM with tool calling
                                                main.py        FastAPI: /api/chat, /api/stats, UI
```

**Hybrid RAG with tool calling.** The LLM picks from four tools:
- `search_listings`: structured filters (country, city, type, bedrooms, price, area, keyword)
- `market_stats`: counts and size statistics over the 60k Wasalt listings
- `semantic_search`: vector search over 307 description and page chunks
- `get_listing`: full detail for one project

Structured questions ("4-bed villas in Oman") go through filters, which vector search handles poorly. Descriptive questions go through semantic search. If a model fails at tool calling, the app falls back to plain retrieve-then-answer.

**LLM:** free OpenRouter models with automatic fallback: `nvidia/nemotron-3-ultra-550b-a55b:free`, then `nvidia/nemotron-3.5-super-vl:free`, then `openrouter/free`. Configure with `OPENROUTER_MODELS`.

**Embeddings:** `BAAI/bge-small-en-v1.5` via fastembed (ONNX, no PyTorch). The index is built at image build time. The image is about 460 MB.

## Deployment

Deployed on **Render** (free Docker web service) via [`render.yaml`](render.yaml). `OPENROUTER_API_KEY` is set as a private environment variable in Render.

## Run locally

```bash
cp .env.example .env            # add your OPENROUTER_API_KEY
docker build -t estate-assistant .
docker run -p 7860:7860 --env-file .env estate-assistant
# open http://localhost:7860
```

To re-scrape (optional):

```bash
pip install -r requirements-scrape.txt && playwright install chromium
python -m scraper.darglobal
python -m scraper.wasalt_sitemap
python -m scraper.normalize
```

## Limitations

- DarGlobal does not publish prices, so the bot answers "price on request".
- Wasalt market data has no prices or cities (see the scraping note above).
- Free OpenRouter models are rate-limited. Busy periods may return a "try again" message.
