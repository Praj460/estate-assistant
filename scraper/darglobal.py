"""Scrape DarGlobal (darglobal.co.uk): every project page + key info pages.

Output: data/raw/darglobal.jsonl  (one JSON record per page)
Run:    python -m scraper.darglobal
"""
import json

from scraper.common import RAW, Browser, next_data, polite_sleep

BASE = "https://darglobal.co.uk"

# Company / info pages useful for "about DarGlobal" questions.
INFO_PAGES = [
    "/about", "/why-invest", "/faq", "/investor", "/hospitality", "/commercial",
    "/luxury-golf-communities", "/one-of-one", "/exclusive-membership", "/tokenization",
    "/partners/aston-martin", "/partners/elie-saab", "/partners/fendi-casa",
    "/partners/lamborghini", "/partners/marriott-residences-aida-oman", "/partners/missoni",
    "/partners/mouawad", "/partners/pagani", "/partners/paramount",
    "/partners/the-trump-organization", "/partners/w-hotels", "/press",
]
MAX_PRESS = 15


def project_index(b: Browser):
    """Read the /projects page: returns list of project cards (title, location, link...)."""
    _, html = b.get(BASE + "/projects")
    nd = next_data(html)
    cards = []
    for tab in nd["props"]["pageProps"]["pageData"]["attributes"]["Tabs"]:
        for p in tab["TabData"]:
            img = (((p.get("ProjectCardImage") or {}).get("data") or {}).get("attributes") or {})
            cards.append({
                "country": tab["TabHeading"],
                "title": (p.get("ProjectTitle") or "").strip(),
                "location": (p.get("Location") or "").strip(),
                "property_type": p.get("ProjectType"),
                "bedrooms": (p.get("Bedrooms") or "").strip() or None,
                "completion": p.get("completionDate"),
                "link": p["Link"],
                "image_url": img.get("url"),
            })
    return cards


def scrape_page(b: Browser, path: str):
    status, html = b.get(BASE + path)
    if not html:
        return None
    nd = next_data(html) or {}
    page_props = nd.get("props", {}).get("pageProps", {})
    page_props.pop("globalData", None)  # site-wide menus/footer, same on every page
    return {
        "url": BASE + path,
        "status": status,
        "title": b.page.title(),
        "meta_description": b.page.evaluate(
            "() => document.querySelector('meta[name=description]')?.content || ''"),
        "text": b.visible_text(),
        "next_data": page_props,
    }


def main():
    out = RAW / "darglobal.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():  # resume support
        done = {json.loads(l)["url"] for l in out.open()}

    with Browser() as b, out.open("a") as f:
        cards = project_index(b)
        print(f"{len(cards)} projects found")
        (RAW / "darglobal_projects_index.json").write_text(json.dumps(cards, indent=1))

        press = []
        jobs = [("project", c["link"], c) for c in cards] + [("page", p, None) for p in INFO_PAGES]
        i = 0
        while i < len(jobs):
            kind, path, card = jobs[i]
            i += 1
            if BASE + path in done:
                continue
            polite_sleep()
            rec = scrape_page(b, path)
            if not rec:
                print(f"  FAILED {path}")
                continue
            rec["kind"] = kind
            rec["card"] = card
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            print(f"  [{i}/{len(jobs)}] {kind:7} {path}  ({len(rec['text'])} chars)")
            # Discover press-release links from /press.
            if path == "/press":
                links = b.page.eval_on_selector_all(
                    "a[href^='/press/']", "els => [...new Set(els.map(e => e.getAttribute('href')))]")
                press = [l for l in links if l.count("/") == 2][:MAX_PRESS]
                jobs += [("press", l, None) for l in press]
    print("done ->", out)


if __name__ == "__main__":
    main()
