"""
Phase 3 (batch): for each video URL from Phase 2, visit the Shorts page and
extract the shopping sticker. Reuses one Chrome instance across all URLs
(MUCH faster than spinning a fresh browser per video) and saves results
incrementally so a mid-run crash doesn't lose work.

Reuses extraction primitives from `poc_shopping_sticker.py`.

Run:
    python phase3_extract.py
    python phase3_extract.py --timeout 15 --delay 3
"""
from __future__ import annotations

import argparse
import json
import sys
import time
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


def load_videos() -> list[dict]:
    if not INPUT_PATH.exists():
        print(f"[err] {INPUT_PATH} not found — run phase2_youtube_search.py first.", file=sys.stderr)
        sys.exit(1)
    payload = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    videos = payload.get("videos", [])
    if not videos:
        print(f"[err] {INPUT_PATH} has no videos.", file=sys.stderr)
        sys.exit(1)
    return videos


def save_partial(results: list[dict], total: int) -> None:
    """Persist what we have so a crash mid-run doesn't lose progress."""
    DATA_DIR.mkdir(exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "processed": len(results),
        "total": total,
        "results": results,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--timeout", type=float, default=10.0, help="sticker wait per video (sec)")
    ap.add_argument("--delay", type=float, default=2.0, help="pause between videos (sec)")
    ap.add_argument("--settle", type=float, default=1.5, help="post-load settle time (sec)")
    ap.add_argument("--headless", action="store_true", help="hide Chrome window")
    ap.add_argument("--limit", type=int, default=0, help="cap videos processed (0 = all)")
    args = ap.parse_args()

    videos = load_videos()
    if args.limit:
        videos = videos[: args.limit]

    print(f"[cfg] {len(videos)} video(s)  timeout={args.timeout}s  delay={args.delay}s")

    driver = build_driver(headless=args.headless)
    results: list[dict] = []
    hit_count = 0

    try:
        for i, video in enumerate(videos, 1):
            url = video.get("video_url")
            vid = video.get("video_id")
            title = (video.get("title") or "")[:50]
            if not url:
                continue

            print(f"\n[{i}/{len(videos)}] {vid}  {title}")

            products: list[dict] = []
            try:
                driver.get(url)
                winner = wait_for_any_sticker(driver, timeout=args.timeout)
                if winner:
                    print(f"  [hit] sticker via '{winner}'")
                else:
                    print(f"  [skip] no sticker detected within {args.timeout}s")

                time.sleep(args.settle)
                products = extract_products(driver)
                if products:
                    hit_count += 1
                    for p in products[:2]:
                        name = (p.get("name") or "?")[:40]
                        print(f"        → {name}  {p.get('price', '-')}")
            except Exception as e:
                print(f"  [err] {e}", file=sys.stderr)

            results.append({
                "video_id": vid,
                "video_url": url,
                "products": products,
            })
            save_partial(results, len(videos))

            if i < len(videos):
                time.sleep(args.delay)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print(f"\n[ok] processed {len(results)} video(s) -> {OUTPUT_PATH}")
    print(f"     {hit_count} had shopping sticker(s)")


if __name__ == "__main__":
    main()
