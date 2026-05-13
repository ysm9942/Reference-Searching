"""
Phase 1: Extract trending YouTube search queries from Google Trends.

Mirrors the Google Trends UI settings the user pointed at:

    seed keyword:  e.g. "여름 옷"
    location:      South Korea  (geo='KR')
    timeframe:     지난 1주     (timeframe='now 7-d')
    search type:   YouTube 검색 (gprop='youtube')   ← THIS is what makes
                                                       the result reflect
                                                       YouTube-side demand
                                                       rather than general
                                                       Google web search.

For each seed, pulls BOTH the "상위 검색어" (top) and "급상승 검색어"
(rising) lists that the UI shows side by side. Each keyword gets tagged
with which list it came from so Phase 2 / the UI can distinguish.

Note: pytrends has been flaky since Google's late-2024 API changes; we
catch errors per-seed and continue rather than abort the whole run.

Run:
    python phase1_trends.py
    python phase1_trends.py --seed "여름 옷"
    python phase1_trends.py --seed "여름 옷" --seed "남자 코디" --geo KR
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from pytrends.request import TrendReq


# Default seed matches the example in the user's screenshot.
DEFAULT_SEEDS = ["여름 옷"]
DATA_DIR = Path("data")
OUTPUT_PATH = DATA_DIR / "phase1_keywords.json"


def fetch_seed(
    pytrends: TrendReq, seed: str, geo: str, timeframe: str, gprop: str
) -> list[dict]:
    """Get both top + rising related queries for one seed. [] on failure."""
    try:
        pytrends.build_payload(
            [seed], cat=0, timeframe=timeframe, geo=geo, gprop=gprop
        )
        related = pytrends.related_queries()
    except Exception as e:
        print(f"  [err] '{seed}': {e}", file=sys.stderr)
        return []

    if not related or seed not in related:
        print(f"  [skip] '{seed}': no related queries", file=sys.stderr)
        return []

    out: list[dict] = []
    for category in ("top", "rising"):
        df = related[seed].get(category)
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            raw_value = row.get("value")
            if isinstance(raw_value, (int, float)):
                growth: int | str | None = int(raw_value)
            elif raw_value is None:
                growth = None
            else:
                growth = str(raw_value)
            out.append(
                {
                    "keyword": row["query"],
                    "growth": growth,
                    "category": category,  # 'top' or 'rising'
                    "seed": seed,
                }
            )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--seed", action="append", default=None, help="seed keyword (repeatable)")
    ap.add_argument("--geo", default="KR", help="region code (default KR)")
    ap.add_argument("--timeframe", default="now 7-d", help="pytrends timeframe (default 'now 7-d')")
    ap.add_argument(
        "--gprop",
        default="youtube",
        choices=("", "youtube", "images", "news", "froogle"),
        help="Google property: 'youtube' = YouTube search trends (default), "
        "'' = general web search",
    )
    ap.add_argument("--top", type=int, default=40, help="max keywords to output")
    ap.add_argument("--locale", default="ko-KR", help="pytrends hl param")
    args = ap.parse_args()

    seeds = args.seed or DEFAULT_SEEDS
    print(
        f"[cfg] seeds={seeds}  geo={args.geo}  "
        f"timeframe={args.timeframe}  gprop={args.gprop or 'web'}"
    )

    pytrends = TrendReq(hl=args.locale, tz=540, timeout=(10, 25))

    aggregated: list[dict] = []
    seen: set[str] = set()

    for seed in seeds:
        print(f"[fetch] seed='{seed}'")
        for kw in fetch_seed(pytrends, seed, args.geo, args.timeframe, args.gprop):
            if kw["keyword"] in seen:
                continue
            seen.add(kw["keyword"])
            aggregated.append(kw)

    aggregated = aggregated[: args.top]

    if not aggregated:
        print(
            "\n[warn] no keywords extracted. pytrends is often rate-limited;\n"
            "       try again in a few minutes, or seed manually with --seed.",
            file=sys.stderr,
        )

    DATA_DIR.mkdir(exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "seeds": seeds,
            "geo": args.geo,
            "timeframe": args.timeframe,
            "gprop": args.gprop,
            "top": args.top,
        },
        "keywords": aggregated,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[ok] {len(aggregated)} keyword(s) -> {OUTPUT_PATH}")
    top_count = sum(1 for k in aggregated if k.get("category") == "top")
    rising_count = sum(1 for k in aggregated if k.get("category") == "rising")
    print(f"     {top_count} top + {rising_count} rising")
    for k in aggregated[:15]:
        cat = k.get("category", "?")
        print(f"  [{cat:7}] {k['keyword']:<25} growth={k.get('growth')}")


if __name__ == "__main__":
    main()
