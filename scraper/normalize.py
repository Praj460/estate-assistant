"""Turn raw scraped data into the clean, unified files the chatbot uses.

Inputs  (data/raw):       darglobal.jsonl, wasalt.jsonl (optional), wasalt_sitemap.jsonl
Outputs (data/processed): listings.jsonl  - one record per property/project (common schema)
                          pages.jsonl     - DarGlobal company/partner/press pages
                          wasalt_market.jsonl - lightweight sitemap records for market stats
Run:    python -m scraper.normalize
"""
import collections
import html
import json
import re

from scraper.common import RAW, ROOT

OUT = ROOT / "data" / "processed"

# Buttons / UI labels that carry no information.
UI_LINES = {
    "FIND OUT MORE", "ACCEPT", "DOWNLOAD BROCHURE", "BOOK UNIT", "REGISTER YOUR INTEREST",
    "VIEW ALL IMAGES", "VIEW ALL PROJECTS", "VIEW ALL", "KNOW MORE", "ENQUIRE NOW", "GALLERY",
    "PHOTO GALLERY", "OTHER PROJECTS", "CHOOSE YOUR RESIDENCE", "READ MORE", "SUBMIT",
}
FIELD_LABELS = {
    "PROPERTY TYPE": "property_type_detail", "STATUS": "status", "AREA (SQM)": "area_sqm_text",
    "AREA (SQFT)": "area_sqft_text", "UNIT TYPE": "unit_type", "UNITS": "unit_type",
    "LOCATION": "location_detail",
}
SECTION_STOP = re.compile(r"^[A-Z0-9 ,&'|()\-]{4,}$")  # an ALL-CAPS heading line

COUNTRY_CODES = {"United Arab Emirates": "UAE", "United Kingdom": "UK"}


def clean_lines(text, boilerplate):
    out = []
    for line in text.split("\n"):
        s = re.sub(r"\s+", " ", line).strip()
        if not s or s in UI_LINES or s in boilerplate:
            continue
        out.append(s)
    return out


def site_boilerplate(records, share=0.5):
    """Lines that appear on more than `share` of pages (cookie banner, newsletter, etc.)."""
    c = collections.Counter()
    for r in records:
        c.update({re.sub(r"\s+", " ", l).strip() for l in r["text"].split("\n")})
    return {l for l, n in c.items() if l and n > share * len(records)}


def parse_fields(lines):
    fields, amenities = {}, []
    for i, s in enumerate(lines):
        key = FIELD_LABELS.get(s)
        if key and i + 1 < len(lines) and key not in fields:
            fields[key] = lines[i + 1]
        if s == "AMENITIES":
            for nxt in lines[i + 1:i + 40]:
                if SECTION_STOP.match(nxt) and nxt not in ("AMENITIES",):
                    break
                if len(nxt) <= 40:
                    amenities.append(nxt)
            amenities = amenities[1:] if amenities and len(amenities[0]) > 25 else amenities
    return fields, amenities


def norm_darglobal():
    raw = [json.loads(l) for l in (RAW / "darglobal.jsonl").open()]
    boiler = site_boilerplate(raw)
    listings, pages = [], []
    for r in raw:
        lines = clean_lines(r["text"], boiler)
        if r["kind"] == "project":
            c = r["card"]
            fields, amenities = parse_fields(lines)
            slug = c["link"].strip("/").replace("/", "-").lower()
            listings.append({
                "id": f"darglobal-{slug}",
                "source": "darglobal",
                "title": c["title"],
                "listing_type": "off-plan / new development",
                "property_type": c["property_type"],
                "country": COUNTRY_CODES.get(c["country"], c["country"]),
                "city": c["location"],
                "district": None,
                "price": None,
                "currency": None,
                "price_note": "Price on request (not published on DarGlobal website)",
                "bedrooms": c["bedrooms"],
                "area_sqm": fields.get("area_sqm_text"),
                "area_sqft": fields.get("area_sqft_text"),
                "status": fields.get("status") or ("Completed" if c["completion"] == "Completed" else None),
                "completion": c["completion"],
                "unit_types": fields.get("unit_type"),
                "amenities": amenities,
                "description": r["meta_description"],
                "full_text": "\n".join(lines),
                "url": r["url"],
                "image_url": c["image_url"],
            })
        else:
            pages.append({
                "id": "darglobal-page-" + r["url"].split("darglobal.co.uk/")[-1].replace("/", "-"),
                "source": "darglobal",
                "kind": r["kind"],
                "title": r["title"].split("|")[0].strip()
                or r["url"].rstrip("/").split("/")[-1].replace("-", " ").title(),
                "url": r["url"],
                "description": r["meta_description"],
                "text": "\n".join(lines),
            })
    return listings, pages


def strip_html(s):
    s = re.sub(r"<br\s*/?>|</p>|</li>", "\n", s or "")
    return re.sub(r"\n\s*\n+", "\n", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def norm_wasalt():
    path = RAW / "wasalt.jsonl"
    if not path.exists():
        return []
    out = []
    for l in path.open():
        r = json.loads(l)
        i = r["info"]
        attrs = {a["key"]: a["value"] for a in r["attributes"]}
        extra = {a["label"]: a["value"] for a in r["additional"] if a.get("value") not in (None, "", "-")}
        price = i.get("conversionPrice") or i.get("salePrice") or i.get("expectedRent")
        try:
            price = float(price) if price not in (None, "") else None
        except ValueError:
            price = None
        imgs = ((r.get("gallery") or {}).get("images") or {}).get("data") or []
        out.append({
            "id": f"wasalt-{r['id']}",
            "source": "wasalt",
            "title": i.get("title"),
            "listing_type": i.get("propertyFor"),
            "property_type": i.get("propertySubType"),
            "country": i.get("country") or "Saudi Arabia",
            "city": i.get("city"),
            "district": i.get("district"),
            "address": i.get("address"),
            "price": price,
            "currency": i.get("currencyType") or "SAR",
            "rent_frequency": i.get("rentFreq") or None,
            "bedrooms": attrs.get("noOfBedrooms"),
            "bathrooms": attrs.get("noOfBathrooms"),
            "area_sqm": attrs.get("builtUpArea") or attrs.get("landArea") or extra.get("Land Area"),
            "furnishing": i.get("furnishingType"),
            "facing": i.get("facingType"),
            "features": extra,
            "description": strip_html(i.get("description")),
            "lat": (r.get("location") or {}).get("lat"),
            "lon": (r.get("location") or {}).get("lon"),
            "listed_date": (r.get("published_at") or "")[:10] or None,
            "agency": r.get("agency"),
            "url": r["url"],
            "image_file": imgs[0].get("content") if imgs else None,  # CDN filename only
        })
    return out


RESIDENTIAL_UNITS = {"apartment", "villa", "floor", "room", "duplex", "rest house"}


def clean_market(r):
    """Fix data-entry artefacts in sitemap-derived records.

    URLs can't contain '.', so '296.09 sqm' appears as '29609-sqm'. A residential
    unit over 10,000 sqm is almost always such a case -> divide by 100.
    """
    a, t = r.get("area_sqm"), r["property_type"]
    if a is not None:
        if t in RESIDENTIAL_UNITS and a > 10_000:
            a = round(a / 100, 2)
        if a > 10_000_000 or a < 5:
            a = None
    r["area_sqm"] = a
    if r.get("bedrooms") is not None and not 0 < r["bedrooms"] <= 20:
        r["bedrooms"] = None
    return r


def write(name, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows):6} -> data/processed/{name}")


def main():
    dg_listings, dg_pages = norm_darglobal()
    wa_listings = norm_wasalt()
    write("listings.jsonl", dg_listings + wa_listings)
    write("pages.jsonl", dg_pages)
    src = RAW / "wasalt_sitemap.jsonl"
    if src.exists():
        write("wasalt_market.jsonl", [clean_market(json.loads(l)) for l in src.open()])


if __name__ == "__main__":
    main()
