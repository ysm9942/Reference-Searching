"""
Phase 2: Search YouTube Shorts for each keyword from Phase 1, ranked by view count.

Uses the official YouTube Data API v3. Reads data/phase1_keywords.json, calls
search.list for each keyword (Shorts-biased: `q="<kw> #shorts"`,
`videoDuration=short`), then videos.list to pull view counts, and filters
by `--min-views`. Output: data/phase2_videos.json.

Quota cost per keyword: search.list = 100 units + 1 unit for videos.list batch.
With the free 10k/day quota you can process ~90 keywords/day.

Run:
    python phase2_youtube_search.py
    python phase2_youtube_search.py --per-keyword 10 --min-views 500000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


DATA_DIR = Path("data")
INPUT_PATH = DATA_DIR / "phase1_keywords.json"
OUTPUT_PATH = DATA_DIR / "phase2_videos.json"


def load_keywords() -> list[str]:
    if not INPUT_PATH.exists():
        print(f"[err] {INPUT_PATH} not found — run phase1_trends.py first.", file=sys.stderr)
        sys.exit(1)
    payload = json.loads(INPUT_PATH.read_text(encoding="utf-8"))
    keywords = [k["keyword"] for k in payload.get("keywords", [])]
    if not keywords:
        print(f"[err] {INPUT_PATH} has no keywords — Phase 1 returned empty.", file=sys.stderr)
        sys.exit(1)
    return keywords


def search_shorts(youtube, keyword: str, max_results: int, region: str) -> list[str]:
    """Return up to `max_results` video IDs for a Shorts-biased search."""
    try:
        resp = (
            youtube.search()
            .list(
                q=f"{keyword} #shorts",
                part="snippet",
                type="video",
                videoDuration="short",
                maxResults=max_results,
                order="viewCount",
                regionCode=region,
                relevanceLanguage="ko",
            )
            .execute()
        )
    except HttpError as e:
        print(f"  [err] search '{keyword}': {e}", file=sys.stderr)
        return []
    return [item["id"]["videoId"] for item in resp.get("items", []) if item.get("id", {}).get("videoId")]


def fetch_video_details(youtube, video_ids: list[str]) -> list[dict]:
    if not video_ids:
        return []
    try:
        resp = (
            youtube.videos()
            .list(part="snippet,statistics,contentDetails", id=",".join(video_ids))
            .execute()
        )
    except HttpError as e:
        print(f"  [err] videos.list: {e}", file=sys.stderr)
        return []

    out: list[dict] = []
    for item in resp.get("items", []):
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})
        out.append(
            {
                "video_id": item["id"],
                "video_url": f"https://www.youtube.com/shorts/{item['id']}",
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "channel_id": snippet.get("channelId", ""),
                "published_at": snippet.get("publishedAt", ""),
                "view_count": int(stats.get("viewCount", 0)),
                "like_count": int(stats.get("likeCount", 0)),
                "comment_count": int(stats.get("commentCount", 0)),
                "duration": item.get("contentDetails", {}).get("duration", ""),
                "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            }
        )
    return out


def main() -> None:
    load_dotenv()
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        print(
            "[err] YOUTUBE_API_KEY not set.\n"
            "      Copy .env.example to .env and paste your API key.\n"
            "      Get a key at https://console.cloud.google.com/ (see .env.example for steps).",
            file=sys.stderr,
        )
        sys.exit(1)

    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--per-keyword", type=int, default=10, help="videos to fetch per keyword")
    ap.add_argument("--min-views", type=int, default=100_000, help="filter threshold")
    ap.add_argument("--region", default="KR", help="regionCode for search.list")
    args = ap.parse_args()

    keywords = load_keywords()
    print(f"[cfg] {len(keywords)} keyword(s)  per-keyword={args.per_keyword}  min-views={args.min_views:,}")

    youtube = build("youtube", "v3", developerKey=api_key, cache_discovery=False)

    # video_id -> dict (deduped across keywords; keep highest-view version)
    bucket: dict[str, dict] = {}

    for i, kw in enumerate(keywords, 1):
        print(f"[search] ({i}/{len(keywords)}) '{kw}'")
        ids = search_shorts(youtube, kw, args.per_keyword, args.region)
        if not ids:
            continue
        for v in fetch_video_details(youtube, ids):
            v["matched_keyword"] = kw
            existing = bucket.get(v["video_id"])
            if existing is None or v["view_count"] > existing["view_count"]:
                bucket[v["video_id"]] = v

    filtered = [v for v in bucket.values() if v["view_count"] >= args.min_views]
    filtered.sort(key=lambda v: v["view_count"], reverse=True)

    DATA_DIR.mkdir(exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "per_keyword": args.per_keyword,
            "min_views": args.min_views,
            "region": args.region,
            "keyword_count": len(keywords),
        },
        "videos": filtered,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[ok] {len(filtered)} video(s) passed filter -> {OUTPUT_PATH}")
    for v in filtered[:5]:
        title = v["title"][:55] + ("..." if len(v["title"]) > 55 else "")
        print(f"  {v['view_count']:>12,}  {title}")


if __name__ == "__main__":
    main()
