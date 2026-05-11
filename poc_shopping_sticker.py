"""
PoC: Extract YouTube Shopping sticker data from a single Shorts URL.

Uses `undetected_chromedriver` (a patched Selenium driver) instead of stock
Chrome/Chromium because YouTube aggressively fingerprints headful Selenium
and headless Chromium. Real Chrome under uc looks like a normal user session.

Run:
    python poc_shopping_sticker.py "https://www.youtube.com/shorts/XXXXXXXXXXX"

Optional flags:
    --headless      run without visible browser window (less stealthy)
    --no-debug      skip saving debug_page.html / debug_page.png
    --timeout N     seconds to wait for the shopping sticker (default 12)
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


# Selector fallback chain — tried in order. Confirmed against real DOM
# (Shorts ID ovAFK2ASguw, 2026-05). The real product sticker lives inside
# `.ytOverlayProductStickerHost`; the bare `<yt-overlay-product-sticker>`
# element elsewhere on the page is an empty placeholder — do not match it.
PRODUCT_STICKER_SELECTORS: list[str] = [
    ".ytOverlayProductStickerHost",
    "yt-overlay-sticker.ytOverlayStickerHost",
    "[class*='OverlayProductSticker']",
    "yt-overlay-product-sticker-view-model",
    "[class*='ShoppingProduct']",
]


def parse_alt_text(alt: str | None) -> dict:
    """
    Real-world format (KR Shorts, confirmed):
        "<name>, ₩<price> 제휴사, 판매처: <seller>"
    Also tolerated:
        "<name>, $<price> ..."   (USD)
        "<name>, <price>원 ..."  (legacy)
    """
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


def _find_one(parent, selector: str):
    try:
        return parent.find_element(By.CSS_SELECTOR, selector)
    except WebDriverException:
        return None


def extract_products(driver: WebDriver) -> list[dict]:
    """Walk the selector chain; return the first chain link that yields hits."""
    for selector in PRODUCT_STICKER_SELECTORS:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
        except WebDriverException as e:
            print(f"[err] query {selector!r}: {e}", file=sys.stderr)
            continue

        if not elements:
            continue

        print(f"[hit] '{selector}' matched {len(elements)} element(s)")
        results: list[dict] = []
        for el in elements:
            link_el = (
                _find_one(el, "a.ytOverlayProductStickerImageContainer")
                or _find_one(el, "a[href]")
            )
            href = link_el.get_attribute("href") if link_el else None

            # Only `.ytImageStickerImageActual` carries the real alt text;
            # the other ~8 img copies have alt="" as a visual stack effect.
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

            parsed = parse_alt_text(alt)
            parsed["link"] = href
            parsed["matched_selector"] = selector
            results.append(parsed)

        if results:
            return results

    return []


def wait_for_any_sticker(driver: WebDriver, timeout: float = 12.0) -> str | None:
    """Poll the selector chain until one attaches, or timeout."""
    end = time.time() + timeout
    while time.time() < end:
        for selector in PRODUCT_STICKER_SELECTORS:
            try:
                if driver.find_elements(By.CSS_SELECTOR, selector):
                    return selector
            except WebDriverException:
                continue
        time.sleep(0.4)
    return None


def build_driver(headless: bool) -> WebDriver:
    options = uc.ChromeOptions()
    options.add_argument("--lang=ko-KR")
    options.add_argument("--window-size=412,915")  # Shorts is mobile-shaped
    if headless:
        # uc supports headless but it's more fingerprintable. Opt-in only.
        options.add_argument("--headless=new")
    # uc auto-downloads a matching chromedriver; version_main=None = auto-detect
    return uc.Chrome(options=options, version_main=None)


def run(url: str, *, headless: bool, save_debug: bool, timeout: float) -> list[dict]:
    driver = build_driver(headless=headless)
    try:
        print(f"[nav] {url}")
        driver.get(url)

        winner = wait_for_any_sticker(driver, timeout=timeout)
        if winner:
            print(f"[wait] sticker attached via: {winner}")
        else:
            print(f"[warn] no shopping sticker selector matched within {timeout}s")

        # Let dynamic content settle past the first paint
        time.sleep(2)

        products = extract_products(driver)

        if save_debug:
            Path("debug_page.html").write_text(driver.page_source, encoding="utf-8")
            driver.save_screenshot("debug_page.png")
            print("[dbg] saved debug_page.html + debug_page.png")

        return products
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
    ap.add_argument("--no-debug", action="store_true", help="skip saving debug_page.{html,png}")
    ap.add_argument("--timeout", type=float, default=12.0, help="seconds to wait for sticker")
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
