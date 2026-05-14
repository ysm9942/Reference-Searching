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
    URLs in YouTube's embedded JSON often arrive with leftover JS escape
    sequences (`\\u0026`, `\\u003d`, …) that we need decoded to the real
    characters. We try two strategies because the source occasionally mixes
    escape conventions within a single page:

      1) explicit replacement for the handful of `\\uXXXX` codes seen in
         affiliate URLs — safe even if some other layer already decoded part
         of the string.
      2) `unicode_escape` codec for anything else that slipped through.
    """
    if not url:
        return url

    # Use chr(0x5c) to write a literal backslash, dodging any tool-level
    # interpretation of `\\uXXXX` inside this source file.
    bs = chr(0x5c)
    for src, dst in (
        (bs + "u0026", "&"),
        (bs + "u003d", "="),
        (bs + "u003c", "<"),
        (bs + "u003e", ">"),
        (bs + "u002f", "/"),
        (bs + "/", "/"),
    ):
        if src in url:
            url = url.replace(src, dst)

    try:
        return url.encode("latin-1", errors="ignore").decode("unicode_escape")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return url


_IMAGE_HOSTS = ("gstatic.com", "ytimg.com", "ggpht.com")


def _is_image_url(url: str | None) -> bool:
    """True if the URL clearly points at an image rather than a buy page."""
    if not url:
        return False
    if "/shopping?q=tbn" in url:
        return True
    return any(host in url for host in _IMAGE_HOSTS)


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


# productSticker = the actual creator-tagged product overlay (what we want).
# productListItemRenderer = items in the "shopping shelf" / discovery feed,
# which YouTube populates with ad-style recommendations unrelated to the
# video content. Earlier versions of this PoC scraped the latter, which
# caused random products (치즈돈까스, 파충류사육상자, etc.) to be matched
# to unrelated fashion videos. We now read ONLY productSticker.
_LABEL_RE = re.compile(
    r'"accessibility"\s*:\s*\{\s*"label"\s*:\s*"([^"]+)"'
)
_SELLER_LABEL_RE = re.compile(r"판매처\s*:\s*(.+?)\s*$")
_PRICE_LABEL_RE = re.compile(r"(₩\s*[\d,]+|\$\s*[\d,]+(?:\.\d{1,2})?)")
_THUMB_SOURCE_RE = re.compile(
    r'"image"\s*:\s*\{\s*"sources"\s*:\s*\[\s*\{\s*"url"\s*:\s*"([^"]+)"'
)
# URL is inside imageAction.command.commandExecutorCommand.commands[1].urlEndpoint.url
# (or webCommandMetadata.url). Match either via "urlEndpoint" or "webCommandMetadata"
# context, restricted to known affiliate redirector hosts to avoid feedback
# /youtubei/v1/feedback URLs leaking in.
_AFFILIATE_URL_RE = re.compile(
    r'"url"\s*:\s*"(https?://(?:link\.coupang\.com|www\.coupang\.com|'
    r'click\.linkprice\.com|search\.shopping\.naver\.com|smartstore\.naver\.com|'
    r'gmarket\.co\.kr|11st\.co\.kr|kakao\.com|googleadservices\.com)[^"]+)"'
)
# Fallback: any non-internal URL (excludes /youtubei/* feedback endpoints).
_EXTERNAL_URL_RE = re.compile(r'"url"\s*:\s*"(https?://(?!www\.youtube\.com)[^"]+)"')


def _parse_label(label: str) -> tuple[str | None, str | None, str | None]:
    """Pull (name, price, seller) out of YouTube's accessibility-label format."""
    if not label:
        return None, None, None
    seller_m = _SELLER_LABEL_RE.search(label)
    seller = seller_m.group(1).strip() if seller_m else None
    if seller in ("", None):
        seller = None

    price_m = _PRICE_LABEL_RE.search(label)
    price = price_m.group(1).strip() if price_m else None

    if price_m:
        name = label[: price_m.start()].rstrip(" ,·-—|").strip()
    else:
        # No price found — keep everything before "판매처:" as name
        name = label
        if seller_m:
            name = label[: seller_m.start()].rstrip(" ,·-—|").strip()
    return name or None, price, seller


def extract_products_from_json(html: str) -> list[dict]:
    """
    Find every `productSticker` block (the actual creator-tagged product
    overlay on the Shorts video) and pull name / price / seller / link /
    thumbnail. One video may carry 0-N stickers depending on how the
    creator tagged it.
    """
    decoded = _decode_escapes(html)
    products: list[dict] = []
    seen_links: set[str] = set()

    for m in re.finditer(r'"productSticker"\s*:\s*\{', decoded):
        brace_start = decoded.find("{", m.end() - 1)
        if brace_start < 0:
            continue
        end = _match_braces(decoded, brace_start, limit=15_000)
        if end < 0:
            continue
        block = decoded[brace_start:end]

        label_m = _LABEL_RE.search(block)
        if not label_m:
            continue  # Not a real sticker — no accessibility label
        label = label_m.group(1)
        name, price, seller = _parse_label(label)
        if not name:
            continue

        thumb_m = _THUMB_SOURCE_RE.search(block)
        thumbnail = _clean_url(thumb_m.group(1)) if thumb_m else None

        # Prefer affiliate domains; fall back to any external URL.
        link_m = _AFFILIATE_URL_RE.search(block)
        if not link_m:
            for fallback in _EXTERNAL_URL_RE.finditer(block):
                if not _is_image_url(fallback.group(1)):
                    link_m = fallback
                    break
        link = _clean_url(link_m.group(1)) if link_m else None

        # Dedup if YT serializes the same sticker twice in different contexts
        dedup_key = link or name
        if dedup_key in seen_links:
            continue
        seen_links.add(dedup_key)

        products.append(
            {
                "name": name,
                "price": price,
                "seller": seller,
                "thumbnail": thumbnail,
                "link": link,
                "raw_alt": label,
                "matched_selector": "json:productSticker",
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
    marker = "productSticker"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if marker in driver.page_source:
                return "json:productSticker"
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
        marker = "productSticker"
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
