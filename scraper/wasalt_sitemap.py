"""Build a market-level Wasalt dataset from its public sitemap (linked in robots.txt).

Every listing URL encodes basic facts, e.g.
  /en/property/sale/villa-1050-sqm-facing-north-west-on-30m-width-street-5888671
  -> listing_type=sale, property_type=villa, area_sqm=1050, facing=north-west, street_width_m=30

Output: data/raw/wasalt_sitemap.jsonl
Run:    python -m scraper.wasalt_sitemap
"""
import gzip
import json
import re
import urllib.request
import xml.etree.ElementTree as ET

from scraper.common import RAW

SITEMAP_URL = "https://cdn.wasalt.sa/sitemap/product_sitemap_en_sa.xml.gz"
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}

URL_RE = re.compile(r"/property/(?P<listing>sale|rent)/(?P<slug>.+)-(?P<id>\d+)$")
TYPE_RE = re.compile(r"^(rest-house|apartment|villa|land|floor|building|room|duplex|"
                     r"townhouse|chalet|farm|office|shop|warehouse|showroom|penthouse|studio)")
AREA_RE = re.compile(r"(\d+(?:\.\d+)?)-sqm")
BEDS_RE = re.compile(r"(\d+)-bedrooms?")
FACING_RE = re.compile(r"facing-((?:north|south|east|west)(?:-(?:north|south|east|west))?|\d+-streets)")
STREET_RE = re.compile(r"on-(\d+(?:\.\d+)?)m-width-street")


def fetch_xml():
    req = urllib.request.Request(SITEMAP_URL, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=120).read()
    if data[:2] == b"\x1f\x8b":  # real gzip vs. served-as-plain
        data = gzip.decompress(data)
    return ET.fromstring(data)


def parse_url(url, lastmod):
    m = URL_RE.search(url)
    if not m:
        return None
    slug = m["slug"]
    t = TYPE_RE.match(slug)
    num = lambda r: (float(x.group(1)) if (x := r.search(slug)) else None)
    beds = BEDS_RE.search(slug)
    facing = FACING_RE.search(slug)
    return {
        "id": f"wasalt-{m['id']}",
        "listing_type": m["listing"],
        "property_type": t.group(1).replace("-", " ") if t else "other",
        "area_sqm": num(AREA_RE),
        "bedrooms": int(beds.group(1)) if beds else None,
        "facing": facing.group(1).replace("-", " ") if facing else None,
        "street_width_m": num(STREET_RE),
        "lastmod": lastmod,
        "url": url,
    }


def main():
    root = fetch_xml()
    out = RAW / "wasalt_sitemap.jsonl"
    n = 0
    with out.open("w") as f:
        for u in root.findall("s:url", NS):
            rec = parse_url(u.findtext("s:loc", namespaces=NS), u.findtext("s:lastmod", namespaces=NS))
            if rec:
                f.write(json.dumps(rec) + "\n")
                n += 1
    print(f"{n} listings -> {out}")


if __name__ == "__main__":
    main()
