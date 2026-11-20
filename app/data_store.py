"""Loads the processed datasets and implements the structured tools the LLM can call."""
import json
import re
import statistics
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "processed"


def _load(name):
    p = DATA / name
    return [json.loads(l) for l in p.open()] if p.exists() else []


def _nums(text):
    return [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", str(text or ""))]


def _range(value):
    """'1 to 3 Bedrooms' -> (1, 3); 4 -> (4, 4); None -> (None, None)."""
    n = _nums(value)
    return (min(n), max(n)) if n else (None, None)


class DataStore:
    def __init__(self):
        self.listings = _load("listings.jsonl")
        self.pages = _load("pages.jsonl")
        self.market = _load("wasalt_market.jsonl")
        for r in self.listings:
            r["_beds"] = _range(r.get("bedrooms"))
            area = r.get("area_sqm") or (
                # convert sqft ranges to sqm for comparability
                " - ".join(str(round(x * 0.092903)) for x in _nums(r.get("area_sqft"))) or None)
            r["_area"] = _range(area)
        self.by_id = {r["id"]: r for r in self.listings}

    # ---------- helpers ----------
    @staticmethod
    def brief(r):
        """Compact view of a listing for the LLM (no long text)."""
        keep = ("id", "source", "title", "listing_type", "property_type", "country", "city",
                "district", "price", "currency", "rent_frequency", "price_note", "bedrooms",
                "bathrooms", "area_sqm", "area_sqft", "status", "completion", "unit_types",
                "furnishing", "listed_date", "url")
        out = {k: r[k] for k in keep if r.get(k) not in (None, "", [])}
        if r.get("amenities"):
            out["amenities"] = r["amenities"][:8]
        if r.get("description"):
            out["summary"] = r["description"][:300]
        return out

    # ---------- tool: search_listings ----------
    def search_listings(self, source=None, country=None, city=None, listing_type=None,
                        property_type=None, min_bedrooms=None, max_bedrooms=None,
                        min_price=None, max_price=None, min_area_sqm=None, max_area_sqm=None,
                        keyword=None, sort_by=None, limit=8):
        def has(field, needle):
            return needle is None or needle.lower() in str(field or "").lower()

        res = []
        for r in self.listings:
            if source and r["source"] != source.lower():
                continue
            if not (has(r.get("country"), country) and has(r.get("listing_type"), listing_type)
                    and has(r.get("property_type"), property_type)):
                continue
            if city and not (has(r.get("city"), city) or has(r.get("district"), city)
                             or has(r.get("address"), city)):
                continue
            lo, hi = r["_beds"]
            if min_bedrooms is not None and (hi is None or hi < min_bedrooms):
                continue
            if max_bedrooms is not None and (lo is None or lo > max_bedrooms):
                continue
            p = r.get("price")
            if min_price is not None and (p is None or p < min_price):
                continue
            if max_price is not None and (p is None or p > max_price):
                continue
            alo, ahi = r["_area"]
            if min_area_sqm is not None and (ahi is None or ahi < min_area_sqm):
                continue
            if max_area_sqm is not None and (alo is None or alo > max_area_sqm):
                continue
            if keyword:
                # Own fields only: full_text also lists "other projects", causing false matches.
                blob = " ".join(str(r.get(k, "")) for k in
                                ("title", "description", "amenities", "features", "unit_types",
                                 "property_type", "city")).lower()
                if not all(w in blob for w in keyword.lower().split()):
                    continue
            res.append(r)

        if sort_by in ("price_asc", "price_desc"):
            res = [r for r in res if r.get("price")]
            res.sort(key=lambda r: r["price"], reverse=sort_by == "price_desc")
        elif sort_by == "newest":
            res.sort(key=lambda r: r.get("listed_date") or "", reverse=True)

        prices = [r["price"] for r in res if r.get("price")]
        summary = {"total_matches": len(res)}
        if prices:
            summary.update(min_price=min(prices), max_price=max(prices),
                           median_price=statistics.median(prices))
        limit = max(1, min(int(limit or 8), 15))
        return {"summary": summary, "results": [self.brief(r) for r in res[:limit]]}

    # ---------- tool: market_stats ----------
    def market_stats(self, listing_type=None, property_type=None, min_area_sqm=None,
                     max_area_sqm=None, bedrooms=None, facing=None, group_by=None):
        """Aggregate stats over all ~60k Wasalt listings in the public sitemap."""
        rows = []
        for r in self.market:
            if listing_type and r["listing_type"] != listing_type.lower():
                continue
            if property_type and property_type.lower().rstrip("s") not in r["property_type"]:
                continue
            a = r.get("area_sqm")
            if min_area_sqm is not None and (a is None or a < min_area_sqm):
                continue
            if max_area_sqm is not None and (a is None or a > max_area_sqm):
                continue
            if bedrooms is not None and r.get("bedrooms") != int(bedrooms):
                continue
            if facing and (r.get("facing") or "") != facing.lower():
                continue
            rows.append(r)

        areas = [r["area_sqm"] for r in rows if r.get("area_sqm")]
        out = {
            "dataset": "Wasalt public sitemap (Saudi Arabia), all active listings, Nov 2026; "
                       "contains type/size/bedrooms/facing only, no prices or cities",
            "count": len(rows),
        }
        if areas:
            out["area_sqm"] = {"median": statistics.median(areas), "min": min(areas), "max": max(areas)}
        key = group_by if group_by in ("listing_type", "property_type", "bedrooms", "facing") else None
        if key:
            groups = {}
            for r in rows:
                groups[str(r.get(key))] = groups.get(str(r.get(key)), 0) + 1
            out["by_" + key] = dict(sorted(groups.items(), key=lambda kv: -kv[1])[:15])
        out["example_urls"] = [r["url"] for r in rows[:3]]
        return out

    # ---------- tool: get_listing ----------
    def get_listing(self, listing_id):
        r = self.by_id.get(listing_id)
        if not r:
            return {"error": f"no listing with id {listing_id}"}
        out = self.brief(r)
        for k in ("full_text", "description", "features", "amenities", "address"):
            if r.get(k):
                out[k] = r[k][:3500] if isinstance(r[k], str) else r[k]
        return out

    def overview(self):
        from collections import Counter
        return {
            "darglobal_projects": sum(r["source"] == "darglobal" for r in self.listings),
            "darglobal_countries": dict(Counter(r["country"] for r in self.listings
                                                if r["source"] == "darglobal")),
            "wasalt_detailed_listings": sum(r["source"] == "wasalt" for r in self.listings),
            "wasalt_market_listings": len(self.market),
            "darglobal_pages": len(self.pages),
        }
