"""Shared helpers for the scrapers: a polite, single-session Playwright browser."""
import json
import random
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# JS run in the page to get readable text without menus/footers/scripts.
VISIBLE_TEXT_JS = """
() => {
  // Remove chrome from the LIVE page (innerText needs layout to keep line breaks).
  document.querySelectorAll('script,style,noscript,svg,header,footer,nav,form,iframe').forEach(e => e.remove());
  return document.body.innerText.replace(/[ \\t]+\\n/g, '\\n').replace(/\\n{3,}/g, '\\n\\n').trim();
}
"""


def next_data(html: str):
    """Return the parsed Next.js __NEXT_DATA__ blob from a page, or None."""
    m = NEXT_DATA_RE.search(html)
    return json.loads(m.group(1)) if m else None


def polite_sleep(lo=3.0, hi=6.0):
    time.sleep(random.uniform(lo, hi))


class Browser:
    """One browser + one page reused for every request (keeps cookies, low request rate)."""

    def __init__(self, block_media=True, headless=True):
        self.block_media = block_media
        self.headless = headless

    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self.page = self._browser.new_page()
        if self.block_media:
            # Skip images/fonts/video: faster and lighter on the target site.
            self.page.route(
                "**/*",
                lambda r: r.abort()
                if r.request.resource_type in ("image", "font", "media")
                else r.continue_(),
            )
        return self

    def get(self, url, settle_ms=2500, retries=2):
        """Load a URL and return (status, html). Retries if a challenge page is shown."""
        for attempt in range(retries + 1):
            try:
                resp = self.page.goto(url, timeout=45000, wait_until="domcontentloaded")
            except Exception as e:
                print(f"   load error on {url}: {e.__class__.__name__}")
                self.page.wait_for_timeout(5000)
                continue
            self.page.wait_for_timeout(settle_ms)
            if self._challenged():
                # Like a normal visitor: stay on the page and let the check finish.
                print(f"   browser check on {url}, waiting...")
                try:
                    self.page.wait_for_function(
                        "() => !document.title.includes('Just a moment')"
                        " && !document.documentElement.outerHTML.slice(0,3000).includes('_Incapsula_Resource')",
                        timeout=25000)
                    self.page.wait_for_load_state("domcontentloaded")
                    self.page.wait_for_timeout(settle_ms)
                except Exception:
                    self.page.wait_for_timeout(10000 * (attempt + 1))
                    continue
            if not self._challenged():
                return (resp.status if resp else None), self.page.content()
        return None, None

    def _challenged(self):
        try:
            return ("Just a moment" in self.page.title()
                    or "_Incapsula_Resource" in self.page.content()[:3000])
        except Exception:  # page navigating mid-check
            return True

    def visible_text(self):
        return self.page.evaluate(VISIBLE_TEXT_JS)

    def __exit__(self, *exc):
        self._browser.close()
        self._pw.stop()
