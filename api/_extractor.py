"""
Self-contained shopping-sticker extractor for Vercel serverless context.
Mirrors poc_shopping_sticker.py:extract_products_from_json but lives in api/
so Vercel can bundle it with the function without pulling in selenium / uc.

NOTE on creator-tag vs algorithmic recommendation:
    YouTube returns a `productSticker` JSON block even on videos with no
    creator-tagged shopping — in that case the block is filled with an
    algorithmic recommendation pulled from a different video. The "key"
    field inside each productSticker is a base64 URL-safe encoded
    protobuf containing the SOURCE video's ID. If that ID doesn't match
    the video we're fetching, the sticker is not creator-tagged and we
    drop it.
"""
from __future__ import annotations

import base64
import re
from urllib.parse import unquote


# JS-level hex escapes used by YouTube inside the embedded JSON strings.
_ESCAPE_MAP = {
    chr(0x5c) + "x22": '"',
    chr(0x5c) + "x7b": "{",
    chr(0x5c) + "x7d": "}",
    chr(0x5c) + "x5b": "[",
    chr(0x5c) + "x5d": "]",
    chr(0x5c) + "x26": "&",
    chr(0x5c) + "x3d": "=",
    chr(0x5c) + "/": "/",
}


def _decode_escapes(s: str) -> str:
    for old, new in _ESCAPE_MAP.items():
        s = s.replace(old, new)
    return s


def _clean_url(url):
    if not url:
        return url
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


def _is_image_url(url) -> bool:
    if not url:
        return False
    if "/shopping?q=tbn" in url:
        return True
    return any(host in url for host in _IMAGE_HOSTS)


def _match_braces(text: str, start: int, limit: int = 15_000) -> int:
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


_LABEL_RE = re.compile(r'"accessibility"\s*:\s*\{\s*"label"\s*:\s*"([^"]+)"')
_SELLER_LABEL_RE = re.compile(r"판매처\s*:\s*(.+?)\s*$")
_PRICE_LABEL_RE = re.compile(
    r"(US\s*\$\s*[\d,]+(?:\.\d{1,2})?|"  # US$58.35 (Vercel US region)
    r"₩\s*[\d,]+|"                        # ₩59,300
    r"\$\s*[\d,]+(?:\.\d{1,2})?|"        # $58.35
    r"[\d,]+\s*원)"                       # 59,300원
)
_THUMB_SOURCE_RE = re.compile(
    r'"image"\s*:\s*\{\s*"sources"\s*:\s*\[\s*\{\s*"url"\s*:\s*"([^"]+)"'
)
_AFFILIATE_URL_RE = re.compile(
    r'"url"\s*:\s*"(https?://(?:link\.coupang\.com|www\.coupang\.com|'
    r'click\.linkprice\.com|search\.shopping\.naver\.com|smartstore\.naver\.com|'
    r'gmarket\.co\.kr|11st\.co\.kr|kakao\.com|googleadservices\.com|zigzag\.kr)[^"]+)"'
)
_EXTERNAL_URL_RE = re.compile(r'"url"\s*:\s*"(https?://(?!www\.youtube\.com)[^"]+)"')


def _parse_label(label: str):
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
        name = label
        if seller_m:
            name = label[: seller_m.start()].rstrip(" ,·-—|").strip()
    return name or None, price, seller


_KEY_RE = re.compile(r'"key"\s*:\s*"([^"]+)"')


def _sticker_belongs_to(block: str, video_id: str) -> bool:
    """
    The productSticker's `key` is a URL-encoded base64 protobuf carrying the
    source video ID. If it doesn't match the video we're scraping, this
    sticker is an algorithmic recommendation pulled from a different video.
    """
    if not video_id:
        return True  # caller didn't ask us to filter — keep everything
    m = _KEY_RE.search(block)
    if not m:
        # No key field at all — can't verify. Be permissive (some videos
        # may have stickers without keys in older API responses).
        return True
    encoded = unquote(m.group(1))
    # Cheap substring check on the still-encoded key first.
    if video_id in encoded:
        return True
    # Real check: base64 url-safe decode (pad-tolerant) and look for the ID.
    for pad in ("", "=", "==", "==="):
        try:
            raw = base64.urlsafe_b64decode(encoded + pad)
        except Exception:
            continue
        if video_id.encode("ascii", errors="ignore") in raw:
            return True
        break
    return False


def extract_products(html: str, video_id: str | None = None):
    """
    Return list of products extracted from `productSticker` blocks in `html`.
    If `video_id` is given, only stickers whose embedded source-video-ID
    matches it are returned (filtering out YouTube's algorithmic
    cross-video shopping recommendations).
    """
    decoded = _decode_escapes(html)
    products = []
    seen_keys = set()

    for m in re.finditer(r'"productSticker"\s*:\s*\{', decoded):
        brace_start = decoded.find("{", m.end() - 1)
        if brace_start < 0:
            continue
        end = _match_braces(decoded, brace_start)
        if end < 0:
            continue
        block = decoded[brace_start:end]

        # Reject stickers whose key points at a different video — those
        # are YouTube's "you might also shop for…" recommendations, not
        # the creator's tag.
        if not _sticker_belongs_to(block, video_id):
            continue

        label_m = _LABEL_RE.search(block)
        if not label_m:
            continue
        label = label_m.group(1)
        name, price, seller = _parse_label(label)
        if not name:
            continue

        thumb_m = _THUMB_SOURCE_RE.search(block)
        thumbnail = _clean_url(thumb_m.group(1)) if thumb_m else None

        link_m = _AFFILIATE_URL_RE.search(block)
        if not link_m:
            for fallback in _EXTERNAL_URL_RE.finditer(block):
                if not _is_image_url(fallback.group(1)):
                    link_m = fallback
                    break
        link = _clean_url(link_m.group(1)) if link_m else None

        dedup_key = link or name
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        products.append({
            "name": name,
            "price": price,
            "seller": seller,
            "thumbnail": thumbnail,
            "link": link,
            "raw_alt": label,
        })

    return products
