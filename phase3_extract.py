"""
Phase 3 (parallel batch): visit each video URL from Phase 2 and extract
the embedded shopping JSON. Runs N Chrome instances in parallel via a
ThreadPoolExecutor to cut wall-clock time roughly Nx.

Since we read the data out of `page_source` (`productListItemRenderer`
JSON blocks), headless Chrome works fine — there's no video playback or
visible sticker dependency. Headless makes parallel runs cheaper on RAM
and avoids 4 visible browser windows fighting for the screen.

Run:
    python phase3_extract.py
    python phase3_extract.py --workers 4 --headless --limit 30
    python phase3_extract.py --workers 6 --timeout 5 --delay 0.5
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

from poc_shopping_sticker import (
    build_driver,
    extract_products,
    wait_for_any_sticker,
)


DATA_DIR = Path("data")
INPUT_PATH = DATA_DIR / "phase2_videos.json"
OUTPUT_PATH = DATA_DIR / "phase3_products.json"

# Thread-safe printing so worker logs don't interleave mid-line
_print_lock = threading.Lock()

# uc patches the chromedriver binary at every Chrome() init and races on
# the patched-binary path when invoked concurrently. We serialize the
# initialization (the actual page-fetch loop still runs in parallel).
_driver_init_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def build_driver_serialized(headless: bool, worker_id: int):
    """build_driver() but with a lock so workers don't fight over chromedriver.exe."""
    with _driver_init_lock:
        log(f"[w{worker_id}] initializing driver…")
        # Brief settle so the previous worker's file handles fully release
        time.sleep(0.3)
        driver = build_driver(headless=headless)
        log(f"[w{worker_id}] driver ready")
        return driver


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


def process_one(driver, video: dict, timeout: float, settle: float) -> dict:
    """Visit one URL, extract products. Returns result dict."""
    url = video.get("video_url")
    vid = video.get("video_id")
    products: list[dict] = []
    if not url:
        return {"video_id": vid, "video_url": url, "products": []}

    try:
        driver.get(url)
        winner = wait_for_any_sticker(driver, timeout=timeout)
        if winner and settle > 0:
            time.sleep(settle)
        products = extract_products(driver)
    except Exception as e:
        log(f"  [err] {vid}: {e}")
        products = []

    return {"video_id": vid, "video_url": url, "products": products}


def worker(
    worker_id: int,
    chunk: list[dict],
    headless: bool,
    timeout: float,
    settle: float,
    delay: float,
) -> list[dict]:
    """A worker thread: creates its own driver, processes chunk, returns results."""
    log(f"[w{worker_id}] starting · {len(chunk)} video(s)")
    try:
        driver = build_driver_serialized(headless=headless, worker_id=worker_id)
    except Exception as e:
        log(f"[w{worker_id}] FAILED to start driver: {e}")
        return []

    results: list[dict] = []
    try:
        for i, video in enumerate(chunk, 1):
            r = process_one(driver, video, timeout, settle)
            results.append(r)

            n_products = len(r["products"])
            title = (video.get("title") or "")[:42]
            status = f"+{n_products} products" if n_products else "no sticker"
            log(f"[w{worker_id}] {i:>2}/{len(chunk)} {r['video_id']}  ({status})  {title}")

            if i < len(chunk):
                time.sleep(delay)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    log(f"[w{worker_id}] done")
    return results


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--workers", type=int, default=4, help="parallel Chrome instances (default 4)")
    ap.add_argument("--headless", action="store_true", default=True, help="run Chrome headless (default: on)")
    ap.add_argument("--no-headless", dest="headless", action="store_false", help="show Chrome windows (debugging)")
    ap.add_argument("--timeout", type=float, default=5.0, help="page wait per video (sec)")
    ap.add_argument("--delay", type=float, default=0.3, help="pause between videos in same worker (sec)")
    ap.add_argument("--settle", type=float, default=0.0, help="post-load settle time (sec)")
    ap.add_argument("--limit", type=int, default=0, help="cap videos processed (0 = all)")
    args = ap.parse_args()

    videos = load_videos()
    if args.limit:
        videos = videos[: args.limit]

    n_workers = max(1, min(args.workers, len(videos)))

    log(
        f"[cfg] {len(videos)} video(s)  workers={n_workers}  "
        f"headless={args.headless}  timeout={args.timeout}s  delay={args.delay}s"
    )

    # Pre-warm: first uc.Chrome() may download a patched chromedriver
    # (~10-20s). Doing this once in the main thread avoids N parallel
    # workers racing for the same download.
    log("[init] pre-warming chromedriver (one-time setup)")
    t0 = time.time()
    try:
        warm = build_driver(headless=True)
        warm.quit()
        log(f"[init] pre-warm done in {time.time()-t0:.1f}s")
    except Exception as e:
        log(f"[init] pre-warm FAILED: {e}")
        sys.exit(1)

    # Interleave videos across workers so per-keyword runs don't all land
    # on one worker (which would create imbalance if some keywords have
    # long videos)
    chunks: list[list[dict]] = [videos[i::n_workers] for i in range(n_workers)]

    started = time.time()
    all_results: list[dict] = []

    with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="p3") as ex:
        futures = {
            ex.submit(worker, i, chunk, args.headless, args.timeout, args.settle, args.delay): i
            for i, chunk in enumerate(chunks)
        }
        for f in as_completed(futures):
            try:
                all_results.extend(f.result())
            except Exception as e:
                log(f"[w{futures[f]}] crashed: {e}")

    # Restore original order so downstream phases get predictable indexing
    by_id = {r["video_id"]: r for r in all_results}
    ordered = [by_id[v["video_id"]] for v in videos if v.get("video_id") in by_id]

    elapsed = time.time() - started
    hits = sum(1 for r in ordered if r.get("products"))
    total_products = sum(len(r.get("products", [])) for r in ordered)

    save_results(ordered, len(videos))

    log("")
    log(f"[ok] processed {len(ordered)}/{len(videos)} video(s) in {elapsed:.1f}s -> {OUTPUT_PATH}")
    log(f"     {hits} had shopping sticker(s) · {total_products} product(s) total")
    if elapsed > 0:
        log(f"     ({len(ordered) / elapsed:.1f} videos/sec average)")


if __name__ == "__main__":
    main()
