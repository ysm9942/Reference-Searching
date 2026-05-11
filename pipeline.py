"""
pipeline.py — run Phase 1 → 2 → 3 → 4 end-to-end.

Each phase is a subprocess so a crash in one stage leaves earlier stages'
output intact (and lets you resume with --skip).

Run:
    python pipeline.py                # all four phases
    python pipeline.py --skip 1       # reuse existing data/phase1_keywords.json
    python pipeline.py --skip 1,2     # only Phase 3 + 4 (re-extract from existing video list)
    python pipeline.py --only 4       # just rebuild web/results.json from existing data/
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PHASES: list[tuple[int, str, str]] = [
    (1, "Phase 1 — Google Trends keywords",        "phase1_trends.py"),
    (2, "Phase 2 — YouTube Data API search",       "phase2_youtube_search.py"),
    (3, "Phase 3 — Shopping sticker extraction",   "phase3_extract.py"),
    (4, "Phase 4 — Consolidate → web/results.json", "phase4_output.py"),
]


def _parse_int_set(raw: str) -> set[int]:
    if not raw:
        return set()
    try:
        return {int(x.strip()) for x in raw.split(",") if x.strip()}
    except ValueError:
        print(f"[err] expected comma-separated integers, got '{raw}'", file=sys.stderr)
        sys.exit(2)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--skip", default="", help="phase numbers to skip, comma-separated (e.g. '1,2')")
    ap.add_argument("--only", default="", help="phase numbers to RUN, comma-separated (overrides --skip)")
    args, passthrough = ap.parse_known_args()

    skip = _parse_int_set(args.skip)
    only = _parse_int_set(args.only)

    bar = "═" * 60
    for num, label, script in PHASES:
        if only and num not in only:
            continue
        if num in skip:
            print(f"\n{bar}\n  SKIP  {label}\n{bar}")
            continue

        print(f"\n{bar}\n  RUN   {label}\n{bar}")
        cmd = [sys.executable, script, *passthrough]
        result = subprocess.run(cmd, cwd=Path.cwd())
        if result.returncode != 0:
            print(f"\n[abort] {label} failed (exit {result.returncode})", file=sys.stderr)
            sys.exit(result.returncode)

    print(f"\n{bar}\n  PIPELINE COMPLETE\n{bar}")
    print("Next steps:")
    print('  git add web/results.json')
    print('  git commit -m "data: $(Get-Date -Format yyyy-MM-dd)"')
    print('  git push')
    print("→ Vercel auto-deploys within ~60s.")


if __name__ == "__main__":
    main()
