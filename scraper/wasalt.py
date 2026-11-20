"""Scrape a random sample of Wasalt (wasalt.sa) sale + rent listings.

Listing URLs come from Wasalt's public sitemap (linked in robots.txt).
Each listing page embeds its data as Next.js JSON; we keep only property
fields and drop personal data (agent/owner names, phone numbers, licences).

Output: data/raw/wasalt.jsonl
Run:    python -m scraper.wasalt [n_sale] [n_rent]
"""
import json
import random
import sys

from scraper.common import RAW, Browser, next_data, polite_sleep

SITEMAP_TXT = RAW / "wasalt_sitemaps" / "product_sitemap_en_sa.txt"


def pick_sample(n_sale, n_rent, seed=42):
    urls = [u.strip() for u in SITEMAP_TXT.open() if u.strip()]
    sale = [u for u in urls if "/property/sale/" in u]
    rent = [u for u in urls if "/property/rent/" in u]
    rng = random.Random(seed)
    return rng.sample(sale, n_sale) + rng.sample(rent, n_rent)


def extract(pp):
    """Keep only non-personal property data from pageProps."""
    d = pp.get("propertyDetailsV3") or {}
    info = d.get("propertyInfo") or {}
    owner = d.get("propertyOwner") or {}
    gallery = pp.get("galleryDetails") or {}
    return {
        "id": d.get("id"),
        "info": {k: info.get(k) for k in (
            "title", "propertyName", "description", "propertyFor", "propertyMainType",
            "propertySubType", "address", "city", "district", "territory", "zone", "country",
            "salePrice", "expectedRent", "rentFreq", "conversionPrice", "currencyType",
            "unitType", "furnishingType", "facingType", "slug")},
        "attributes": [{"key": a.get("key"), "name": a.get("name"), "value": a.get("value")}
                       for a in d.get("attributes") or []],
        "additional": [{"key": a.get("key"), "label": a.get("label"), "value": a.get("value")}
                       for a in d.get("additionalAttributes") or []],
        "location": d.get("location"),
        "published_at": d.get("publishedAt"),
        "updated_at": d.get("updatedAt"),
        "is_verified": d.get("isVerified"),
        "is_rega": d.get("isRegaProp"),
        "agency": owner.get("enCmpName") or owner.get("companyName") or None,
        "user_role": owner.get("enUserRole"),
        "gallery": gallery,  # trimmed to a cover image in parsing
    }


def main():
    n_sale = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    n_rent = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    out = RAW / "wasalt.jsonl"
    done = {json.loads(l)["url"] for l in out.open()} if out.exists() else set()
    urls = [u for u in pick_sample(n_sale, n_rent) if u not in done]
    print(f"{len(urls)} listings to fetch ({len(done)} already done)")

    ok = fail = blocked_in_a_row = 0
    # Visible browser window, human-like pace (10-15 s per listing).
    with Browser(headless=False) as b, out.open("a") as f:
        for i, url in enumerate(urls, 1):
            if blocked_in_a_row >= 3:
                print("STOP: blocked 3 times in a row - respecting the site's protection.")
                break
            polite_sleep(10, 15)
            try:
                status, html = b.get(url, settle_ms=800)
                nd = next_data(html) if html else None
                pp = (nd or {}).get("props", {}).get("pageProps", {})
                if not pp.get("propertyDetailsV3"):
                    raise ValueError(f"no listing data (status {status})")
                rec = {"url": url, **extract(pp)}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                ok += 1
                blocked_in_a_row = 0
                print(f"  ok {i}: {rec['info'].get('title')} | {rec['info'].get('city')}")
            except Exception as e:  # removed/expired listings etc.
                fail += 1
                blocked_in_a_row += 1 if html is None else 0
                print(f"  skip {url}: {e}")
            if i % 25 == 0:
                print(f"  {i}/{len(urls)}  ok={ok} fail={fail}")
    print(f"done ok={ok} fail={fail} -> {out}")


if __name__ == "__main__":
    main()
