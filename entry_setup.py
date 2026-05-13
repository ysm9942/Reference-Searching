"""
Setup wizard for Reference-Searching (designed to be frozen by PyInstaller).

Tkinter GUI that:
  1) verifies Chrome is installed (uc dependency)
  2) collects YouTube Data API v3 key
  3) writes .env, creates data/ and web/ directories alongside the exe

When frozen, files are written next to the exe — so place Setup.exe in the
folder you want as the "install location" before running it.
"""
from __future__ import annotations

import os
import sys
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox


CHROME_CANDIDATES: list[Path] = [
    Path(os.environ.get("ProgramFiles", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    Path(os.environ.get("LocalAppData", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
]


def app_dir() -> Path:
    """Directory containing the running exe (or this script when un-frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def find_chrome() -> Path | None:
    return next((p for p in CHROME_CANDIDATES if p.exists()), None)


class SetupWizard:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Reference-Searching Setup")
        self.root.geometry("540x400")
        self.root.resizable(False, False)
        self.root.configure(bg="#0a0a0a")

        self.api_key_var = tk.StringVar()
        self.chrome_path = find_chrome()
        self._build_ui()

    def _build_ui(self) -> None:
        # ── header ──
        tk.Label(
            self.root,
            text="YouTube Shorts 쇼핑 트렌드 수집기",
            font=("Segoe UI", 14, "bold"),
            bg="#0a0a0a",
            fg="#fafafa",
        ).pack(pady=(20, 4))
        tk.Label(
            self.root,
            text="설치 위치: " + str(app_dir()),
            font=("Segoe UI", 8),
            bg="#0a0a0a",
            fg="#888888",
        ).pack()

        # ── chrome status ──
        chrome_frame = tk.Frame(self.root, bg="#111111", padx=12, pady=10)
        chrome_frame.pack(fill="x", padx=20, pady=(20, 5))
        if self.chrome_path:
            tk.Label(
                chrome_frame,
                text=f"✓  Chrome 감지됨\n{self.chrome_path}",
                bg="#111111",
                fg="#00d97f",
                font=("Segoe UI", 9),
                justify="left",
                anchor="w",
            ).pack(fill="x")
        else:
            tk.Label(
                chrome_frame,
                text="✗  Chrome 이 설치되지 않았습니다 (필수)",
                bg="#111111",
                fg="#ff0033",
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            ).pack(fill="x")
            link = tk.Label(
                chrome_frame,
                text="→ google.com/chrome 에서 설치 후 다시 실행",
                bg="#111111",
                fg="#3b82f6",
                font=("Segoe UI", 9, "underline"),
                cursor="hand2",
                anchor="w",
            )
            link.pack(fill="x", pady=(4, 0))
            link.bind("<Button-1>", lambda _e: webbrowser.open("https://www.google.com/chrome/"))

        # ── api key input ──
        tk.Label(
            self.root,
            text="YouTube Data API v3 키",
            bg="#0a0a0a",
            fg="#fafafa",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x", padx=20, pady=(15, 2))
        tk.Label(
            self.root,
            text="발급: console.cloud.google.com → APIs → YouTube Data API v3 → Credentials",
            bg="#0a0a0a",
            fg="#888888",
            font=("Segoe UI", 8),
            anchor="w",
        ).pack(fill="x", padx=20)

        entry = tk.Entry(
            self.root,
            textvariable=self.api_key_var,
            font=("Consolas", 10),
            bg="#111111",
            fg="#fafafa",
            insertbackground="#fafafa",
            relief="flat",
            highlightthickness=1,
            highlightcolor="#ff0033",
            highlightbackground="#26262c",
        )
        entry.pack(fill="x", padx=20, pady=(8, 0), ipady=6)
        entry.focus_set()

        # ── buttons ──
        btn_frame = tk.Frame(self.root, bg="#0a0a0a")
        btn_frame.pack(side="bottom", fill="x", padx=20, pady=20)

        save_btn = tk.Button(
            btn_frame,
            text="설정 저장",
            command=self._save_and_exit,
            bg="#ff0033",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
            padx=24,
            pady=8,
            cursor="hand2",
            activebackground="#cc0029",
            activeforeground="white",
        )
        save_btn.pack(side="right", padx=(8, 0))

        tk.Button(
            btn_frame,
            text="취소",
            command=self.root.destroy,
            bg="#111111",
            fg="#fafafa",
            font=("Segoe UI", 10),
            relief="flat",
            padx=16,
            pady=8,
            cursor="hand2",
            activebackground="#1f1f24",
            activeforeground="#fafafa",
        ).pack(side="right")

        # Enter key submits
        self.root.bind("<Return>", lambda _e: self._save_and_exit())

    def _save_and_exit(self) -> None:
        key = self.api_key_var.get().strip()
        if not key:
            messagebox.showerror("입력 누락", "YouTube API 키를 입력하세요.")
            return
        if not key.startswith("AIza") or len(key) < 30:
            if not messagebox.askyesno(
                "키 형식 확인",
                "일반적인 Google API 키 형식이 아닙니다 (보통 'AIza' 로 시작).\n"
                "계속 진행할까요?",
            ):
                return

        base = app_dir()
        try:
            (base / "data").mkdir(exist_ok=True)
            (base / "web").mkdir(exist_ok=True)
            env_path = base / ".env"
            env_path.write_text(f"YOUTUBE_API_KEY={key}\n", encoding="utf-8")
        except Exception as e:
            messagebox.showerror("저장 실패", f"파일 쓰기 오류:\n{e}")
            return

        chrome_msg = "" if self.chrome_path else "\n\n⚠ Chrome 이 아직 설치 안 됨 — Pipeline 실행 전에 설치하세요."
        messagebox.showinfo(
            "완료",
            f".env 저장됨:\n{env_path}\n\n이제 Pipeline.exe 를 실행하세요." + chrome_msg,
        )
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    SetupWizard().run()
