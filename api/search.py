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

MAX_RESULTS = 10  # hard cap, even if user asks for more
DEFAULT_RESULTS = 5
HTTP_TIMEOUT = 6.0


def search_youtube(api_key: str, keyword: str, n: int) -> list[dict]:
    """Return up to n Shorts metadata dicts ordered by view count."""
    youtube = build("youtube", "v3", developerKey=api_key, cache_discovery=False)

    search_resp = (
        youtube.search()
        .list(
            q=f"{keyword} #shorts",
            part="snippet",
            type="video",
            videoDuration="short",
            maxResults=n,
            order="viewCount",
            regionCode="KR",
            relevanceLanguage="ko",
        )
        .execute()
    )
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
    """Augment a video dict with `products` extracted from its Shorts page."""
    products = []
    try:
        r = session.get(video["video_url"], timeout=HTTP_TIMEOUT)
        if r.status_code == 200 and r.text:
            products = extract_products(r.text)
    except Exception:
        products = []
    video["products"] = products
    return video


def handle_search(keyword: str, n: int) -> dict:
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        return {"error": "server is missing YOUTUBE_API_KEY env var"}, 500

    n = max(1, min(n, MAX_RESULTS))

    try:
        videos = search_youtube(api_key, keyword, n)
    except Exception as e:
        return {"error": f"youtube search failed: {type(e).__name__}: {e}"}, 502

    if not videos:
        return {
            "keyword": keyword,
            "items": [],
            "note": "no videos returned from YouTube",
        }, 200

    # Parallel fetch + extract — each request is ~1-2s, so n=5 in flight = ~2-3s
    with requests.Session() as session:
        session.headers.update(HTTP_HEADERS)
        with ThreadPoolExecutor(max_workers=min(n, 10)) as ex:
            items = list(ex.map(lambda v: fetch_products_for(v, session), videos))

    items.sort(key=lambda v: v.get("view_count", 0), reverse=True)
    return {"keyword": keyword, "items": items}, 200


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        keyword = (qs.get("q") or [""])[0].strip()
        try:
            n = int((qs.get("n") or [str(DEFAULT_RESULTS)])[0])
        except ValueError:
            n = DEFAULT_RESULTS

        if not keyword:
            self._send_json({"error": "missing required query param 'q'"}, 400)
            return

        body, status = handle_search(keyword, n)
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
