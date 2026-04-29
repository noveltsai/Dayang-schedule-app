"""
soak_check.py — 提醒系統 soak 期檢查

由 GitHub Actions cron 觸發（每年 5/4 10:23 Taipei）。
推一則 Telegram 訊息給菜包，提醒過幾天的提醒實際運作狀態，
並列出當前 active_todos 數量。
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ_TAIPEI = timezone(timedelta(hours=8))


def count_active_todos() -> int | None:
    """讀 Line 對話爬文 active_todos.md 數待辦數。
    本 repo（達揚班表）撈不到那個檔，回 None 由訊息文案處理。"""
    candidates = [
        Path(r"E:/ClaudeProject/Line對話爬文/active_todos.md"),
    ]
    for p in candidates:
        if p.exists():
            text = p.read_text(encoding="utf-8")
            section = re.search(
                r"##\s*待辦中\s*\n(.*?)(?=\n##|\Z)", text, re.S
            )
            if not section:
                return None
            return len(re.findall(r"^###\s+TODO-", section.group(1), re.M))
    return None


def push_telegram(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"[sent] HTTP {resp.status}")


def main() -> int:
    now = datetime.now(TZ_TAIPEI)
    todo_count = count_active_todos()
    todo_line = (
        f"當前 active_todos：{todo_count} 件" if todo_count is not None
        else "（GH Runner 上撈不到 active_todos.md，請手動查）"
    )

    text = (
        f"🐟 Soak 期檢查（{now:%Y-%m-%d}）\n"
        f"\n"
        f"上班前 30 分鐘提醒過去 ~5 天運作得如何？\n"
        f"\n"
        f"幾個自問：\n"
        f"1. 提醒準時響嗎？文案要調整嗎？\n"
        f"2. {todo_line}\n"
        f"3. 要不要啟動「每週日彙整」？\n"
        f"\n"
        f"打開 Claude 報狀態，記憶會接住專案脈絡。"
    )

    push_telegram(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
