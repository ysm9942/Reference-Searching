"""
PoC: Extract YouTube Shopping product data from a single Shorts URL.

Strategy: parse the embedded JSON (`productListItemRenderer` blocks) that
YouTube ships in every Shorts page. The visible "sticker" DOM is rendered
late by JS and varies by A/B test, but the underlying data lives in the
initial HTML as a string-encoded JSON blob. Parsing that is:

  * faster — no need to wait for sticker DOM to appear
  * more stable — backend field names change less than CSS classes
  * complete — captures all products on a video, even ones not rendered yet

If the JSON path turns up nothing (rare — would mean YouTube changed the
schema), we fall back to the legacy DOM-selector walk.

Uses `undetected_chromedriver` because YouTube fingerprints stock Selenium.

Run:
    python poc_shopping_sticker.py "https://www.youtube.com/shorts/XXXXXXXXXXX"

Optional flags:
    --headless      run without visible browser window (less stealthy)
    --no-debug      skip saving debug_page.html / debug_page.png
    --timeout N     seconds to wait for page content (default 8)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import undetected_chromedriver as uc
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver


# ────────────────────────────────────────────────────────────────────────────
# Legacy DOM fallback — kept as a safety net in case the JSON path ever fails.
# ────────────────────────────────────────────────────────────────────────────
PRODUCT_STICKER_SELECTORS: list[str] = [
    ".ytOverlayProductStickerHost",
    "yt-overlay-sticker.ytOverlayStickerHost",
    "[class*='OverlayProductSticker']",
    "yt-overlay-product-sticker-view-model",
    "[class*='ShoppingProduct']",
]


# ────────────────────────────────────────────────────────────────────────────
# Primary extractor: parse productListItemRenderer JSON from page source.
# ────────────────────────────────────────────────────────────────────────────
# The JSON is double-escaped inside a JS string inside HTML. We decode the
# outer layer of `\x22` → `"` etc. before pattern matching.
_ESCAPE_MAP = {
    # JS hex escapes (used by YouTube in HTML-embedded JSON)
    r"\x22": '"',
    r"\x7b": "{",
    r"\x7d": "}",
    r"\x5b": "[",
    r"\x5d": "]",
    r"\x26": "&",
    r"\x3d": "=",
    # JS unicode escapes — these appear inside URL query strings
    r"&": "&",
    r"=": "=",
    r"<": "<",
    r">": ">",
    r"/": "/",
    # Forward-slash convention used in JSON-in-JS
    r"\/": "/",
}


def _decode_escapes(s: str) -> str:
    for old, new in _ESCAPE_MAP.items():
        s = s.replace(old, new)
    return s


def _clean_url(url: str | None) -> str | None:
    """
    URLs in YouTube's embedded JSON contain Python-style escape sequences
    (`\\u0026`, `\\x3d`, etc.) that we want decoded to real characters.
    Python's `unicode_escape` codec handles all of them in one pass.
    """
    if not url:
        return url
    try:
        return url.encode("latin-1", errors="ignore").decode("unicode_escape")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return url


def _match_braces(text: str, start: int, limit: int = 20_000) -> int:
    """Given index of `{`, return index AFTER the matching `}` (or -1)."""
    if start >= len(text) or text[start] != "{":
        return -1
    depth = 0
    end = min(len(text), start + limit)
    in_string = False
    escape = False
    for i in range(start, end):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


_SELLER_RE = re.compile(r"판매처\s*:\s*([^,\"\}]+)")
_PRICE_RE = re.compile(r'"price"\s*:\s*"([^"]+)"')
_TITLE_RE = re.compile(r'"title"\s*:\s*\{\s*"simpleText"\s*:\s*"([^"]+)"')
_ACCESSIBILITY_RE = re.compile(r'"accessibilityTitle"\s*:\s*"([^"]+)"')
_THUMB_RE = re.compile(
    r'"thumbnail"\s*:\s*\{\s*"thumbnails"\s*:\s*\[\s*\{\s*"url"\s*:\s*"([^"]+)"'
)
# Buy link priority: known affiliate domains, then any external URL inside the block.
_AFFILIATE_URL_RE = re.compile(
    r'"url"\s*:\s*"(https?://(?:link\.coupang\.com|www\.coupang\.com|'
    r'click\.linkprice\.com|search\.shopping\.naver\.com|smartstore\.naver\.com|'
    r'gmarket\.co\.kr|11st\.co\.kr|kakao\.com|googleadservices\.com)[^"]+)"'
)
_ANY_URL_RE = re.compile(r'"url"\s*:\s*"(https?://[^"]+)"')


def extract_products_from_json(html: str) -> list[dict]:
    """Find every productListItemRenderer block and pull its fields."""
    decoded = _decode_escapes(html)
    products: list[dict] = []
    seen: set[str] = set()

    for m in re.finditer(r'"productListItemRenderer"\s*:\s*\{', decoded):
        brace_start = decoded.find("{", m.end() - 1)
        if brace_start < 0:
            continue
        end = _match_braces(decoded, brace_start)
        if end < 0:
            continue
        block = decoded[brace_start:end]

        # The first 1-2 matches in any YT page are "renderer-type registries"
        # — JSON lists naming all known renderer classes for client lazy-loading.
        # Filter them out.
        if (
            "compactProductListRenderer" in block
            and "richGridRenderer" in block
            and "simpleText" not in block
        ):
            continue

        title_m = _TITLE_RE.search(block)
        if not title_m:
            continue
        name = title_m.group(1).strip()
        if not name or name in seen:
            continue
        seen.add(name)

        price_m = _PRICE_RE.search(block)
        accessibility_m = _ACCESSIBILITY_RE.search(block)
        thumb_m = _THUMB_RE.search(block)

        seller: str | None = None
        if accessibility_m:
            sm = _SELLER_RE.search(accessibility_m.group(1))
            if sm:
                seller = sm.group(1).strip() or None

        link_m = _AFFILIATE_URL_RE.search(block) or _ANY_URL_RE.search(block)
        link = _clean_url(link_m.group(1)) if link_m else None
        thumbnail = _clean_url(thumb_m.group(1)) if thumb_m else None

        products.append(
            {
                "name": name,
                "price": price_m.group(1).strip() if price_m else None,
                "seller": seller,
                "thumbnail": thumbnail,
                "link": link,
                "raw_alt": accessibility_m.group(1) if accessibility_m else None,
                "matched_selector": "json:productListItemRenderer",
            }
        )

    return products


# ────────────────────────────────────────────────────────────────────────────
# Legacy DOM fallback (kept verbatim from the older PoC).
# ────────────────────────────────────────────────────────────────────────────
def _find_one(parent, selector: str):
    try:
        return parent.find_element(By.CSS_SELECTOR, selector)
    except WebDriverException:
        return None


def _parse_alt_text(alt: str | None) -> dict:
    if not alt:
        return {"raw_alt": None, "name": None, "price": None, "seller": None}
    seller_match = re.search(r"판매처\s*:\s*(.+?)\s*$", alt)
    seller = seller_match.group(1).strip() if seller_match else None
    price_match = re.search(
        r"(₩\s*[\d,]+|\$\s*[\d,]+(?:\.\d{1,2})?|[\d,]+\s*원)", alt
    )
    price = price_match.group(1).strip() if price_match else None
    name = alt
    if price_match:
        name = alt[: price_match.start()].rstrip(" ,·-—|").strip()
    return {"raw_alt": alt, "name": name or None, "price": price, "seller": seller}


def extract_products_from_dom(driver: WebDriver) -> list[dict]:
    for selector in PRODUCT_STICKER_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException as e:
            print(f"[err] query {selector!r}: {e}", file=sys.stderr)
            continue
        if not elements:
            continue
        print(f"[dom-hit] '{selector}' matched {len(elements)} element(s)")
        results: list[dict] = []
        for el in elements:
            link_el = (
                _find_one(el, "a.ytOverlayProductStickerImageContainer")
                or _find_one(el, "a[href]")
            )
            href = link_el.get_attribute("href") if link_el else None
            img_el = _find_one(el, "img.ytImageStickerImageActual")
            alt = img_el.get_attribute("alt") if img_el else None
            if not alt:
                for candidate in el.find_elements(By.CSS_SELECTOR, "img[alt]"):
                    candidate_alt = candidate.get_attribute("alt")
                    if candidate_alt and candidate_alt.strip():
                        alt = candidate_alt
                        break
            if not alt:
                alt = el.get_attribute("aria-label")
            parsed = _parse_alt_text(alt)
            parsed["link"] = href
            parsed["thumbnail"] = None
            parsed["matched_selector"] = f"dom:{selector}"
            results.append(parsed)
        if results:
            return results
    return []


# ────────────────────────────────────────────────────────────────────────────
# Public API — used by phase3_extract.py (batch processor)
# ────────────────────────────────────────────────────────────────────────────
def wait_for_any_sticker(driver: WebDriver, timeout: float = 10.0) -> str | None:
    """
    Block until either the JSON marker `productListItemRenderer` appears in
    page source, or a known sticker selector attaches to the DOM. Returns
    the matched signal so the caller can log which path triggered.
    """
    marker = "productListItemRenderer"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if marker in driver.page_source:
                return "json:productListItemRenderer"
        except WebDriverException:
            pass
        for selector in PRODUCT_STICKER_SELECTORS:
            try:
                if driver.find_elements(By.CSS_SELECTOR, selector):
                    return f"dom:{selector}"
            except WebDriverException:
                continue
        time.sleep(0.4)
    return None


def extract_products(driver: WebDriver) -> list[dict]:
    """JSON first (preferred), DOM walk as fallback."""
    try:
        html = driver.page_source
    except WebDriverException:
        html = ""
    if html:
        products = extract_products_from_json(html)
        if products:
            return products
    return extract_products_from_dom(driver)


# ────────────────────────────────────────────────────────────────────────────
# Driver setup + run loop (single-URL CLI entry)
# ────────────────────────────────────────────────────────────────────────────
def build_driver(headless: bool) -> WebDriver:
    options = uc.ChromeOptions()
    options.add_argument("--lang=ko-KR")
    options.add_argument("--window-size=412,915")
    # Autoplay helps the legacy DOM fallback (sticker rendered only while playing)
    # but isn't needed for the JSON path. Cheap to set either way.
    options.add_argument("--autoplay-policy=no-user-gesture-required")
    options.add_argument("--mute-audio")
    if headless:
        options.add_argument("--headless=new")
    return uc.Chrome(options=options, version_main=None)


def run(url: str, *, headless: bool, save_debug: bool, timeout: float) -> list[dict]:
    driver = build_driver(headless=headless)
    try:
        print(f"[nav] {url}")
        driver.get(url)

        # Shopping data is injected into the page source by YouTube's player
        # code AFTER initial load — sometimes immediately, sometimes after
        # video playback starts. Poll the page source for the marker string
        # rather than guessing a fixed sleep.
        marker = "productListItemRenderer"
        deadline = time.time() + timeout
        html = ""
        found_marker = False
        while time.time() < deadline:
            html = driver.page_source
            if marker in html:
                found_marker = True
                break
            time.sleep(0.5)

        elapsed = timeout - max(0.0, deadline - time.time())
        if found_marker:
            print(f"[poll] '{marker}' appeared after {elapsed:.1f}s")
        else:
            print(f"[poll] '{marker}' not found within {timeout}s")

        if save_debug:
            Path("debug_page.html").write_text(html, encoding="utf-8")
            try:
                driver.save_screenshot("debug_page.png")
            except Exception:
                pass
            print("[dbg] saved debug_page.html + debug_page.png")

        # 1) primary: JSON parse from page source
        products = extract_products_from_json(html)
        if products:
            print(f"[json] extracted {len(products)} product(s) from embedded JSON")
            return products

        # 2) fallback: walk legacy DOM selectors
        print("[json] no products in JSON; falling back to DOM walk")
        return extract_products_from_dom(driver)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("url", help="YouTube Shorts URL (must have shopping enabled)")
    ap.add_argument("--headless", action="store_true", help="run browser headless")
    ap.add_argument("--no-debug", action="store_true", help="skip saving debug artifacts")
    ap.add_argument("--timeout", type=float, default=8.0, help="page wait time (sec)")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    products = run(
        args.url,
        headless=args.headless,
        save_debug=not args.no_debug,
        timeout=args.timeout,
    )
    print("\n=== RESULTS ===")
    print(json.dumps(products, ensure_ascii=False, indent=2))
    print(f"\nfound {len(products)} product(s)")


if __name__ == "__main__":
    main()
