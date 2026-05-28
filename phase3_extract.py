"""
Phase 3 (HTTP, parallel): fetch each Shorts page via plain HTTPS and parse the
embedded `productSticker` JSON directly from the response body.

Why HTTP instead of Chrome:
    Earlier versions launched `undetected_chromedriver` to render the page,
    but the data we extract (`productSticker` JSON) is shipped in the
    initial HTML response. There's no JS rendering required to read it —
    Selenium was paying ~3-10 seconds of Chrome startup + page-load
    overhead per video for no actual benefit.

    Plain `requests.get` returns the same JSON in ~1.5s and works fine in
    parallel via ThreadPoolExecutor. End-to-end on the 81-video set drops
    from ~130s (Chrome × 4 workers) to ~10-20s (HTTP × 20 workers).

A `--use-chrome` flag preserves the old uc.Chrome path as a safety net for
URLs that ever start serving stripped content over plain HTTP.

Run:
    python phase3_extract.py
    python phase3_extract.py --workers 20 --retries 1
    python phase3_extract.py --use-chrome    # legacy path
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

from poc_shopping_sticker import extract_products_from_json


DATA_DIR = Path("data")
INPUT_PATH = DATA_DIR / "phase2_videos.json"
OUTPUT_PATH = DATA_DIR / "phase3_products.json"

# Browser-like headers so YouTube serves the full HTML (not a stripped
# variant or a JSON API response).
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
    "Upgrade-Insecure-Requests": "1",
}

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def load_videos() -> list[dict]:
    if not INPUT_PATH.exists():
        log(f"[err] {INPUT_PATH} not found — run phase2_youtube_search.py first.")
        sys.exit(1)
    payload = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    videos = payload.get("videos", [])
    if not videos:
        log(f"[err] {INPUT_PATH} has no videos.")
        sys.exit(1)
    return videos


def save_results(results: list[dict], total: int) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "processed": len(results),
                "total": total,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ────────────────────────────────────────────────────────────────────────────
# HTTP path (default)
# ────────────────────────────────────────────────────────────────────────────
def fetch_http(
    session: requests.Session, url: str, timeout: float, retries: int
) -> tuple[str | None, int]:
    """Fetch URL via HTTP with optional retries. Returns (html, status_code)."""
    last_status = -1
    for attempt in range(retries + 1):
        try:
            r = session.get(url, timeout=timeout)
            last_status = r.status_code
            if r.status_code == 200 and r.text:
                return r.text, r.status_code
        except requests.RequestException:
            pass
        if attempt < retries:
            time.sleep(0.4)
    return None, last_status


def process_one_http(
    session: requests.Session,
    video: dict,
    timeout: float,
    retries: int,
) -> dict:
    url = video.get("video_url")
    vid = video.get("video_id")
    if not url:
        return {"video_id": vid, "video_url": url, "products": []}
    html, status = fetch_http(session, url, timeout, retries)
    products = extract_products_from_json(html) if html else []
    return {
        "video_id": vid,
        "video_url": url,
        "products": products,
        "http_status": status,
    }


def run_http(videos: list[dict], workers: int, timeout: float, retries: int) -> list[dict]:
    """Parallel HTTP fetcher. Returns results in the same order as `videos`."""
    log(
        f"[cfg] {len(videos)} video(s) | mode=HTTP | "
        f"workers={workers} | timeout={timeout}s | retries={retries}"
    )

    started = time.time()
    # A shared Session enables connection pooling / keep-alive, which is
    # important when hammering one host repeatedly.
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    results: list[dict | None] = [None] * len(videos)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="http") as ex:
        future_to_idx = {
            ex.submit(process_one_http, session, v, timeout, retries): i
            for i, v in enumerate(videos)
        }
        done = 0
        for f in as_completed(future_to_idx):
            idx = future_to_idx[f]
            try:
                r = f.result()
            except Exception as e:
                v = videos[idx]
                log(f"  [err] {v.get('video_id')}: {e}")
                r = {
                    "video_id": v.get("video_id"),
                    "video_url": v.get("video_url"),
                    "products": [],
                    "http_status": -1,
                }
            results[idx] = r
            done += 1
            hit = len(r["products"])
            title = (videos[idx].get("title") or "")[:42]
            tag = f"+{hit}" if hit else "·"
            log(f"  {done:>3}/{len(videos)} [{r.get('http_status','?'):>3}] {tag:>4}  {title}")

            # Save incrementally so a Ctrl-C doesn't wipe everything
            if done % 10 == 0 or done == len(videos):
                save_results([r for r in results if r is not None], len(videos))

    final = [r for r in results if r is not None]
    elapsed = time.time() - started
    hits = sum(1 for r in final if r["products"])
    total_products = sum(len(r["products"]) for r in final)
    log("")
    log(
        f"[ok] {len(final)}/{len(videos)} processed in {elapsed:.1f}s "
        f"({len(final)/max(elapsed, 0.001):.1f} videos/sec)"
    )
    log(f"     {hits} videos with sticker · {total_products} product(s) total")
    return final


# ────────────────────────────────────────────────────────────────────────────
# Chrome path (legacy fallback, kept for --use-chrome)
# ────────────────────────────────────────────────────────────────────────────
def run_chrome(videos: list[dict], workers: int, timeout: float, headless: bool) -> list[dict]:
    """Legacy uc.Chrome-based path. Slower but useful if HTTP ever gets blocked."""
    # Imported lazily so the HTTP path doesn't pay the uc import cost
    from poc_shopping_sticker import (
        build_driver,
        extract_products,
        wait_for_any_sticker,
    )

    _driver_init_lock = threading.Lock()

    def _build_serialized(worker_id: int):
        with _driver_init_lock:
            log(f"[w{worker_id}] initializing chrome…")
            time.sleep(0.3)
            d = build_driver(headless=headless)
            log(f"[w{worker_id}] driver ready")
            return d

    def worker(worker_id: int, chunk: list[dict]) -> list[dict]:
        try:
            driver = _build_serialized(worker_id)
        except Exception as e:
            log(f"[w{worker_id}] FAILED to start driver: {e}")
            return []
        out: list[dict] = []
        try:
            for i, v in enumerate(chunk, 1):
                url = v.get("video_url")
                vid = v.get("video_id")
                products: list[dict] = []
                try:
                    driver.get(url)
                    wait_for_any_sticker(driver, timeout=timeout)
                    products = extract_products(driver)
                except Exception as e:
                    log(f"  [err] {vid}: {e}")
                out.append({"video_id": vid, "video_url": url, "products": products})
                hit = len(products)
                tag = f"+{hit}" if hit else "·"
                title = (v.get("title") or "")[:42]
                log(f"[w{worker_id}] {i:>2}/{len(chunk)} {tag:>4} {vid}  {title}")
        finally:
            try:
                driver.quit()
            except Exception:
                pass
        return out

    log(f"[cfg] {len(videos)} video(s) | mode=CHROME | workers={workers} | headless={headless}")

    # Pre-warm to download the patched chromedriver once
    try:
        warm = build_driver(headless=True)
        warm.quit()
    except Exception as e:
        log(f"[init] pre-warm FAILED: {e}")
        sys.exit(1)

    started = time.time()
    chunks = [videos[i::workers] for i in range(workers)]

    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(worker, i, c) for i, c in enumerate(chunks)]
        for f in as_completed(futures):
            try:
                out.extend(f.result())
            except Exception as e:
                log(f"worker crashed: {e}")

    # Restore original order
    by_id = {r["video_id"]: r for r in out}
    ordered = [by_id[v["video_id"]] for v in videos if v.get("video_id") in by_id]
    save_results(ordered, len(videos))

    elapsed = time.time() - started
    hits = sum(1 for r in ordered if r["products"])
    total_products = sum(len(r["products"]) for r in ordered)
    log("")
    log(f"[ok] {len(ordered)}/{len(videos)} processed in {elapsed:.1f}s")
    log(f"     {hits} videos with sticker · {total_products} product(s) total")
    return ordered


# ────────────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--workers", type=int, default=20, help="parallel workers (default 20 for HTTP, drop to 4 for Chrome)")
    ap.add_argument("--timeout", type=float, default=15.0, help="per-request timeout (sec)")
    ap.add_argument("--retries", type=int, default=1, help="HTTP retries on failure (default 1)")
    ap.add_argument("--limit", type=int, default=0, help="cap videos processed (0 = all)")
    ap.add_argument("--use-chrome", action="store_true", help="use legacy uc.Chrome path (slower)")
    ap.add_argument("--headless", action="store_true", default=True, help="(chrome path) run headless")
    ap.add_argument("--no-headless", dest="headless", action="store_false")
    args = ap.parse_args()

    videos = load_videos()
    if args.limit:
        videos = videos[: args.limit]

    if args.use_chrome:
        workers = min(args.workers, 4)  # Chrome wants fewer workers
        results = run_chrome(videos, workers=workers, timeout=args.timeout, headless=args.headless)
    else:
        results = run_http(videos, workers=args.workers, timeout=args.timeout, retries=args.retries)

    save_results(results, len(videos))


if __name__ == "__main__":
    main()
