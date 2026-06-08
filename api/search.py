"""
Vercel serverless function: GET /api/search?q=<keyword>&n=5

Pipeline (~3-5s for n=5 videos):
    1. YouTube Data API v3 search.list  →  short video IDs (cost: 100 units)
    2. videos.list                       →  view counts + metadata (cost: 1 unit)
    3. parallel HTTP fetch of each Shorts page (ThreadPoolExecutor)
    4. extract productSticker JSON from each response
    5. merge + sort by view count, return as JSON

Required Vercel env var:
    YOUTUBE_API_KEY   (Google Cloud Console → YouTube Data API v3 → API key)
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import requests
from googleapiclient.discovery import build

# Make sibling _extractor.py importable when Vercel bundles this function
sys.path.insert(0, os.path.dirname(__file__))
from _extractor import extract_products  # noqa: E402


HTTP_HEADERS = {
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
    "Sec-Fetch-Dest": "document",
    "Upgrade-Insecure-Requests": "1",
}

MAX_RESULTS = 30  # hard cap, even if user asks for more
DEFAULT_RESULTS = 20
HTTP_TIMEOUT = 6.0

# How many keywords (seed + auto-suggested) we expand a search into.
# Each one costs ~100 YouTube API quota units, so capping at 5 keeps
# us at ~500 units/search → ~20 searches/day on the free 10k quota.
MAX_EXPANSION_KEYWORDS = 5
SUGGEST_TIMEOUT = 4.0

# Map UI period choice → days of lookback for the `publishedAfter`
# parameter on YouTube Data API search.list. `None` = no time filter.
PERIOD_DAYS: dict[str, int | None] = {
    "day":   1,
    "week":  7,
    "month": 30,
    "year":  365,
    "all":   None,
}
DEFAULT_PERIOD = "week"


def fetch_youtube_suggestions(seed: str, lang: str = "ko") -> list[str]:
    """
    Hit YouTube's public autocomplete endpoint and return the suggested
    queries for `seed`. This is the same data source that powers the
    dropdown under YouTube's search bar — closer to "what people actually
    type on YouTube" than Google Trends (which reflects general web
    search). Free, fast (~300ms), no rate-limit headaches.

    Endpoint format with client=firefox returns plain JSON:
        ["meme", ["meme review", "meme song", ...]]
    """
    url = "https://suggestqueries.google.com/complete/search"
    params = {"client": "firefox", "ds": "yt", "q": seed, "hl": lang}
    try:
        r = requests.get(url, params=params, timeout=SUGGEST_TIMEOUT)
        if r.status_code != 200:
            return []
        data = r.json()
        if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], list):
            return [s.strip() for s in data[1] if isinstance(s, str) and s.strip()]
    except Exception:
        pass
    return []


def expand_keywords(seed: str, limit: int = MAX_EXPANSION_KEYWORDS) -> list[str]:
    """Seed + dedup'd YouTube suggestions, capped at `limit`. Seed is always first."""
    out = [seed]
    seen = {seed.lower().strip()}
    for s in fetch_youtube_suggestions(seed):
        norm = s.lower().strip()
        if norm in seen:
            continue
        seen.add(norm)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def search_youtube(api_key: str, keyword: str, n: int, period: str = DEFAULT_PERIOD) -> list[dict]:
    """Return up to n Shorts metadata dicts ordered by view count.
    `period` ∈ PERIOD_DAYS — restricts to videos published within that window."""
    youtube = build("youtube", "v3", developerKey=api_key, cache_discovery=False)

    params = dict(
        q=f"{keyword} #shorts",
        part="snippet",
        type="video",
        videoDuration="short",
        maxResults=n,
        order="viewCount",
        regionCode="KR",
        relevanceLanguage="ko",
    )

    # publishedAfter wants RFC 3339, e.g. "2025-05-13T00:00:00Z"
    days = PERIOD_DAYS.get(period)
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        params["publishedAfter"] = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    search_resp = youtube.search().list(**params).execute()
    video_ids = [
        item["id"]["videoId"]
        for item in search_resp.get("items", [])
        if item.get("id", {}).get("videoId")
    ]
    if not video_ids:
        return []

    details_resp = (
        youtube.videos()
        .list(part="snippet,statistics,contentDetails", id=",".join(video_ids))
        .execute()
    )

    out = []
    for item in details_resp.get("items", []):
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})
        out.append({
            "video_id": item["id"],
            "video_url": f"https://www.youtube.com/shorts/{item['id']}",
            "title": snippet.get("title", ""),
            "channel": snippet.get("channelTitle", ""),
            "published_at": snippet.get("publishedAt", ""),
            "view_count": int(stats.get("viewCount", 0)),
            "thumbnail": (
                snippet.get("thumbnails", {}).get("high", {}).get("url", "")
            ),
            "matched_keyword": keyword,
        })
    return out


def fetch_products_for(video: dict, session: requests.Session) -> dict:
    """Augment a video dict with `products` extracted from its Shorts page.
    Also stamps diagnostic fields so we can see what YouTube actually
    returned (size + presence of the productSticker marker) — useful when
    Vercel's IP gets served stripped HTML."""
    products: list[dict] = []
    http_status = None
    response_size = 0
    has_marker = False
    err = None
    try:
        r = session.get(video["video_url"], timeout=HTTP_TIMEOUT)
        http_status = r.status_code
        response_size = len(r.text) if r.text else 0
        if r.status_code == 200 and r.text:
            has_marker = "productSticker" in r.text
            # Pass video_id so the extractor rejects algorithmic
            # cross-video shopping recommendations.
            products = extract_products(r.text, video_id=video.get("video_id"))
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    video["products"] = products
    video["_diag"] = {
        "http_status": http_status,
        "response_size": response_size,
        "has_marker": has_marker,
        "error": err,
    }
    return video


def handle_search(keyword: str, n: int, period: str) -> dict:
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        return {"error": "server is missing YOUTUBE_API_KEY env var"}, 500

    n = max(1, min(n, MAX_RESULTS))
    if period not in PERIOD_DAYS:
        period = DEFAULT_PERIOD

    # 1) Expand seed keyword via YouTube autocomplete
    keywords = expand_keywords(keyword, limit=MAX_EXPANSION_KEYWORDS)
    # Allocate per-keyword video budget so combined results land near `n`.
    # +1 absorbs duplicates that get merged out.
    per_keyword = max(3, (n // max(len(keywords), 1)) + 1)

    # 2) Run YouTube search for each expanded keyword in parallel
    def _search_one(kw: str) -> tuple[str, list[dict]]:
        try:
            return kw, search_youtube(api_key, kw, per_keyword, period=period)
        except Exception as e:
            return kw, []

    with ThreadPoolExecutor(max_workers=len(keywords)) as ex:
        per_kw_results = list(ex.map(_search_one, keywords))

    # 3) Merge & dedupe by video_id. Each video remembers which keyword
    # surfaced it (the earlier keyword in the expansion wins on ties).
    seen_ids: set[str] = set()
    videos: list[dict] = []
    for kw, vids in per_kw_results:
        for v in vids:
            vid = v.get("video_id")
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)
            v["matched_keyword"] = kw  # which expansion brought this in
            videos.append(v)

    # Cap total before doing the heavier HTML fetch
    videos = videos[:n]

    if not videos:
        return {
            "keyword": keyword,
            "keywords_used": keywords,
            "period": period,
            "items": [],
            "note": "no videos returned from YouTube (across all expanded keywords)",
        }, 200

    # 4) Pull each Shorts page and extract creator-tagged products
    with requests.Session() as session:
        session.headers.update(HTTP_HEADERS)
        with ThreadPoolExecutor(max_workers=min(len(videos), 10)) as ex:
            items = list(ex.map(lambda v: fetch_products_for(v, session), videos))

    items.sort(key=lambda v: v.get("view_count", 0), reverse=True)
    return {
        "keyword": keyword,
        "keywords_used": keywords,
        "period": period,
        "items": items,
        "_debug": {
            "vercel_region": os.environ.get("VERCEL_REGION", "unknown"),
            "period_days": PERIOD_DAYS.get(period),
            "keywords_expanded": len(keywords),
            "items_total": len(items),
            "items_with_marker": sum(1 for i in items if i.get("_diag", {}).get("has_marker")),
            "items_with_creator_tag": sum(1 for i in items if i.get("products")),
            "note": "keywords_used = seed + up to 4 YouTube-suggest autocompletions. "
                    "items_with_creator_tag counts videos whose sticker key embeds the "
                    "same video_id (i.e. genuinely creator-tagged, not algorithmic).",
        },
    }, 200


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        keyword = (qs.get("q") or [""])[0].strip()
        try:
            n = int((qs.get("n") or [str(DEFAULT_RESULTS)])[0])
        except ValueError:
            n = DEFAULT_RESULTS
        period = (qs.get("period") or [DEFAULT_PERIOD])[0].strip().lower()

        if not keyword:
            self._send_json({"error": "missing required query param 'q'"}, 400)
            return

        body, status = handle_search(keyword, n, period)
        self._send_json(body, status)

    def do_OPTIONS(self):
        # CORS preflight (not strictly needed for same-origin, harmless)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _send_json(self, obj, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        # Only cache successful responses. Errors must be re-fetched so a
        # transient failure (e.g. missing env var while it's being set,
        # YouTube quota blip) doesn't get pinned for 5 minutes by the CDN.
        if 200 <= status < 300:
            self.send_header("Cache-Control", "public, max-age=300")
        else:
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Silence default per-request stderr logging in Vercel
        pass
