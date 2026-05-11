"""
Phase 4: merge Phase 2 (video metadata) + Phase 3 (extracted products)
into the single `web/results.json` consumed by the static viewer.

Output shape matches what `web/index.html` expects: top-level `items`
array with products embedded per video, sorted by view count.

Run:
    python phase4_output.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


DATA_DIR = Path("data")
PHASE2_PATH = DATA_DIR / "phase2_videos.json"
PHASE3_PATH = DATA_DIR / "phase3_products.json"
OUTPUT_PATH = Path("web") / "results.json"


def _require(path: Path) -> dict:
    if not path.exists():
        print(f"[err] {path} not found.", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    phase2 = _require(PHASE2_PATH)
    phase3 = _require(PHASE3_PATH)

    # Index phase3 results by video_id for O(1) lookup
    products_by_id: dict[str, list[dict]] = {
        r["video_id"]: r.get("products", [])
        for r in phase3.get("results", [])
        if r.get("video_id")
    }

    items: list[dict] = []
    for v in phase2.get("videos", []):
        vid = v.get("video_id")
        if not vid:
            continue
        items.append({
            "video_id": vid,
            "video_url": v.get("video_url"),
            "title": v.get("title"),
            "channel": v.get("channel"),
            "view_count": v.get("view_count", 0),
            "published_at": v.get("published_at"),
            "thumbnail": v.get("thumbnail"),
            "matched_keyword": v.get("matched_keyword"),
            "products": products_by_id.get(vid, []),
        })

    items.sort(key=lambda x: x.get("view_count", 0), reverse=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with_products = sum(1 for i in items if i["products"])
    total_products = sum(len(i["products"]) for i in items)
    print(f"[ok] {len(items)} video(s) -> {OUTPUT_PATH}")
    print(f"     {with_products} with shopping sticker(s) · {total_products} product(s) total")


if __name__ == "__main__":
    main()
