"""
Pipeline runner for Reference-Searching (designed to be frozen by PyInstaller).

Runs Phase 1 → 2 → 3 → 4 silently. There is no Python console window
(--windowed flag during build), but Chrome WILL appear during Phase 3
because undetected_chromedriver requires a real headed browser session
for its stealth profile.

Logs to `pipeline.log` next to the exe so failures are debuggable without
a console. On Windows, a balloon notification fires when the run finishes.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


# ── working directory must contain .env, data/, web/ ─────────────────────
WORK_DIR = app_dir()
os.chdir(WORK_DIR)
sys.path.insert(0, str(WORK_DIR))


# ── logging ──────────────────────────────────────────────────────────────
LOG_PATH = WORK_DIR / "pipeline.log"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def toast(title: str, body: str) -> None:
    """Best-effort Windows balloon notification. Silent failure if unavailable."""
    try:
        import ctypes  # stdlib; works on Windows only
        ctypes.windll.user32.MessageBoxW(0, body, title, 0x40)  # MB_ICONINFORMATION
    except Exception:
        pass


# ── run one phase by importing its module and calling main() ─────────────
def run_phase(label: str, module_name: str) -> bool:
    log(f"START  {label}")
    original_argv = sys.argv
    sys.argv = [f"{module_name}.py"]
    try:
        mod = __import__(module_name)
        mod.main()
        log(f"OK     {label}")
        return True
    except SystemExit as e:
        if e.code in (None, 0):
            log(f"OK     {label}")
            return True
        log(f"FAIL   {label} (exit code {e.code})")
        return False
    except Exception as e:
        log(f"ERROR  {label}: {e}")
        log("".join(traceback.format_exception(type(e), e, e.__traceback__)))
        return False
    finally:
        sys.argv = original_argv


PHASES: list[tuple[str, str]] = [
    ("Phase 1 — Google Trends keywords",       "phase1_trends"),
    ("Phase 2 — YouTube Data API search",      "phase2_youtube_search"),
    ("Phase 3 — Shopping sticker extraction",  "phase3_extract"),
    ("Phase 4 — Consolidate → web/results.json", "phase4_output"),
]


def main() -> int:
    # Truncate previous log
    try:
        LOG_PATH.write_text("", encoding="utf-8")
    except Exception:
        pass

    log(f"working dir: {WORK_DIR}")
    log(f"frozen={getattr(sys, 'frozen', False)}  python={sys.version.split()[0]}")

    started = time.time()
    failed_phase: str | None = None

    for label, module_name in PHASES:
        if not run_phase(label, module_name):
            failed_phase = label
            break

    elapsed = int(time.time() - started)
    mins, secs = divmod(elapsed, 60)
    summary = f"{mins}분 {secs}초 소요"
    log(f"DONE   ({summary})")

    if failed_phase:
        toast(
            "Reference-Searching — 실패",
            f"{failed_phase}\n에서 중단됨 ({summary})\n\n로그: {LOG_PATH}",
        )
        return 1
    else:
        toast(
            "Reference-Searching — 완료",
            f"파이프라인 완료 ({summary})\n\nweb/results.json 갱신됨\n"
            f"git add web/results.json && git commit && git push 로 Vercel 배포",
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
