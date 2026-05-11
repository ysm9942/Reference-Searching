"""
Phase 1: Extract trending keywords related to memes/viral content.

Uses pytrends to query Google Trends. For each seed keyword (e.g. "meme",
"shorts", "viral"), pulls "rising" related queries — these are searches
that have spiked recently relative to their baseline.

Note: pytrends has been flaky since Google's late-2024 API changes; we
catch errors per-seed and continue rather than abort the whole run.

Run:
    python phase1_trends.py
    python phase1_trends.py --seed 밈 --seed 챌린지 --geo KR --top 30
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from pytrends.request import TrendReq


DEFAULT_SEEDS = ["meme", "shorts", "viral", "밈", "챌린지"]
DATA_DIR = Path("data")
OUTPUT_PATH = DATA_DIR / "phase1_keywords.json"


def fetch_rising(pytrends: TrendReq, seed: str, geo: str, timeframe: str) -> list[dict]:
    """Get rising related queries for one seed. Returns [] on any failure."""
    try:
        pytrends.build_payload([seed], cat=0, timeframe=timeframe, geo=geo)
        related = pytrends.related_queries()
    except Exception as e:
        print(f"  [err] '{seed}': {e}", file=sys.stderr)
        return []

    rising_df = related.get(seed, {}).get("rising") if related else None
    if rising_df is None or rising_df.empty:
        print(f"  [skip] '{seed}': no rising queries", file=sys.stderr)
        return []

    out = []
    for _, row in rising_df.iterrows():
        out.append({
            "keyword": row["query"],
            "growth": int(row["value"]) if str(row["value"]).isdigit() else str(row["value"]),
            "seed": seed,
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--seed", action="append", default=None, help="seed keyword (repeatable)")
    ap.add_argument("--geo", default="KR", help="region code (default KR)")
    ap.add_argument("--timeframe", default="now 7-d", help="pytrends timeframe (default 'now 7-d')")
    ap.add_argument("--top", type=int, default=25, help="max keywords to output")
    ap.add_argument("--locale", default="ko-KR", help="pytrends hl param")
    args = ap.parse_args()

    seeds = args.seed or DEFAULT_SEEDS
    print(f"[cfg] seeds={seeds}  geo={args.geo}  timeframe={args.timeframe}")

    pytrends = TrendReq(hl=args.locale, tz=540, timeout=(10, 25))

    aggregated: list[dict] = []
    seen: set[str] = set()

    for seed in seeds:
        print(f"[fetch] seed='{seed}'")
        for kw in fetch_rising(pytrends, seed, args.geo, args.timeframe):
            if kw["keyword"] in seen:
                continue
            seen.add(kw["keyword"])
            aggregated.append(kw)

    aggregated = aggregated[: args.top]

    if not aggregated:
        print(
            "\n[warn] no keywords extracted. pytrends is often rate-limited;\n"
            "       try again in a few minutes, or run with --seed <your-keyword>.",
            file=sys.stderr,
        )

    DATA_DIR.mkdir(exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "seeds": seeds,
            "geo": args.geo,
            "timeframe": args.timeframe,
            "top": args.top,
        },
        "keywords": aggregated,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[ok] {len(aggregated)} keyword(s) -> {OUTPUT_PATH}")
    for k in aggregated[:10]:
        growth = k.get("growth")
        print(f"  - {k['keyword']:<30} (seed={k['seed']}, growth={growth})")


if __name__ == "__main__":
    main()
