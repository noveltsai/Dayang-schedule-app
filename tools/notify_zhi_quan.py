"""
notify_zhi_quan.py — 上班前 30 分鐘 Telegram 提醒

由 GitHub Actions cron 觸發。讀 docs/data/{YYYY-MM}.json，
判斷今天志銓有沒有班；有就在「開始時間 -30 min ± 20 min」窗口內推 Telegram。

依賴：
- 環境變數：TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID
- 同目錄：shifts.py（共用班表邏輯）
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from shifts import LOC_FULL, get_shift_window, is_off_type  # noqa: E402

TZ_TAIPEI = timezone(timedelta(hours=8))
TARGET_NAME = "志銓"
NOTIFY_LEAD_MIN = 30
TOLERANCE_MIN = 20  # GitHub Actions cron 可能延遲，給 20 分鐘容錯


def load_today_shift() -> tuple[dict | None, datetime]:
    now = datetime.now(TZ_TAIPEI)
    json_path = (
        Path(__file__).parent.parent
        / "docs"
        / "data"
        / f"{now.year}-{now.month:02d}.json"
    )
    if not json_path.exists():
        print(f"[skip] schedule file missing: {json_path.name}")
        return None, now

    data = json.loads(json_path.read_text(encoding="utf-8"))
    matches = [
        s for s in data["shifts"]
        if s.get("name") == TARGET_NAME and s.get("day") == now.day
    ]
    if not matches:
        print(f"[skip] no entry for {TARGET_NAME} on day {now.day}")
        return None, now
    return matches[0], now


def push_telegram(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(f"[sent] HTTP {resp.status}")


def main() -> int:
    shift, now = load_today_shift()
    if shift is None:
        return 0

    type_ = shift.get("type")
    if is_off_type(type_) or not type_:
        print(f"[skip] off day: type={type_!r}")
        return 0

    win = get_shift_window(
        TARGET_NAME,
        shift.get("loc"),
        type_,
        shift.get("hours"),
        weekday=now.weekday(),
    )
    if not win:
        print(f"[skip] no resolvable window: shift={shift}")
        return 0

    start_min, end_min = win
    target_min = start_min - NOTIFY_LEAD_MIN
    now_min = now.hour * 60 + now.minute
    diff = now_min - target_min

    print(
        f"[debug] now={now:%H:%M} start={start_min//60:02d}:{start_min%60:02d} "
        f"target={target_min//60:02d}:{target_min%60:02d} diff={diff} min"
    )

    if abs(diff) > TOLERANCE_MIN:
        print(f"[skip] outside notify window (±{TOLERANCE_MIN} min)")
        return 0

    loc_full = LOC_FULL.get(shift.get("loc", ""), shift.get("loc", "?"))
    text = (
        f"🐟 30 分鐘後上班\n"
        f"\n"
        f"今天：{loc_full} {type_}班 "
        f"({start_min//60:02d}:{start_min%60:02d}–"
        f"{end_min//60:02d}:{end_min%60:02d})\n"
        f"\n"
        f"記得撈 Line 對話（達揚 / 健揚 / (05)），"
        f"丟 NBLM 跑分類後貼回 Claude 勾選。"
    )

    push_telegram(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
