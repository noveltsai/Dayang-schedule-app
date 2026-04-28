"""
shifts.py — 達揚班表共用邏輯

包含：
- SHIFT_TIMES 對照表
- 自訂時段 hours 字串解析
- get_shift_window 取得實際上班時段（處理志銓平日日晚特例）
- compute_coworkers 共班計算（同店、重疊 ≥ 5 小時）
- is_off_type 判斷休假類別
- iter_shifts 把雙頭班展開
"""
from __future__ import annotations

import calendar
from datetime import date
from typing import Iterable, Optional

# ── 時段對照（單位：分鐘 from 00:00）─────────────────────────
def _hm(h: int, m: int = 0) -> int:
    return h * 60 + m


SHIFT_TIMES = {
    "達早": (_hm(9), _hm(17)),
    "達晚": (_hm(14), _hm(22)),
    "達全": (_hm(9), _hm(22)),
    "健早": (_hm(9), _hm(17)),
    "健晚": (_hm(14), _hm(22)),
    "健全": (_hm(9), _hm(22)),
    "日早": (_hm(8), _hm(17)),
    "日晚": (_hm(14), _hm(22)),
    "日全": (_hm(8), _hm(22)),  # 早起機制
}

OFF_TYPES = {"休", "必休", "年假", "補休"}

# loc 全名 ↔ 簡稱
LOC_FULL = {"達": "達揚", "日": "日揚", "健": "健揚"}
LOC_SHORT = {v: k for k, v in LOC_FULL.items()}


def is_off_type(t: Optional[str]) -> bool:
    return t in OFF_TYPES


def parse_hours(s: str) -> Optional[tuple[int, int]]:
    """解析自訂時段字串，回傳 (start_min, end_min)，失敗回傳 None。

    規則：
        - 短碼且小時 < 8 → 整對 +12（下午簡寫），例：「2-9」=14:00-21:00
        - 4 位數或前導 0 或含冒號 → 24h 字面，例：「0900-1500」、「13:30-14:50」

    支援格式：
        "10:00-18:00", "10-18", "1:30-2:50", "13:30-14:50",
        "2-9", "5-9", "1-9", "9-15", "0900-1500", "1500-2200"
    """
    if not s or "-" not in s:
        return None
    try:
        a, b = [x.strip() for x in s.split("-", 1)]
        sh, sm = _split_hm(a)
        eh, em = _split_hm(b)
    except (ValueError, TypeError):
        return None

    # 24h 字面：起首 0（"0900"、"01:30"），或無冒號且 ≥ 4 位（"1500"）
    # 3 位數如「400」仍視為下午簡寫（4 → 16）
    is_24h_literal = a.startswith("0") or (":" not in a and len(a) >= 4)
    if not is_24h_literal and sh < 8:
        sh += 12
        eh += 12

    if not (0 <= sh <= 23 and 0 <= eh <= 23 and 0 <= sm <= 59 and 0 <= em <= 59):
        return None
    start = sh * 60 + sm
    end = eh * 60 + em
    if end <= start:
        return None
    return (start, end)


def _split_hm(t: str) -> tuple[int, int]:
    """支援 'H' / 'HH' / 'HHMM' / 'HMM' / 'H:M' / 'HH:MM'"""
    if ":" in t:
        h, m = t.split(":", 1)
        return int(h), int(m)
    n = int(t)
    if n < 0:
        raise ValueError(t)
    if n <= 23:
        return n, 0
    if 100 <= n <= 2359:
        h, m = divmod(n, 100)
        return h, m
    raise ValueError(f"無法解析時間 {t!r}")


def parse_single_time(t: str) -> Optional[int]:
    """解析單一時間字串（不含 '-'），回傳從 00:00 起算的分鐘數。

    沿用 parse_hours 的下午簡寫規則：
        - 無前導 0、無冒號、長度 < 4，且小時 < 8 → 視為下午（+12）
        - 例：「4」、「400」、「4:30」 → 16:00
        - 例：「0900」、「9」、「9:00」 → 09:00
        - 例：「1500」、「15」 → 15:00
    """
    if not t:
        return None
    try:
        h, m = _split_hm(t.strip())
    except (ValueError, TypeError):
        return None
    is_24h_literal = t.startswith("0") or (":" not in t and len(t) >= 4)
    if not is_24h_literal and h < 8:
        h += 12
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


# 達揚無大夜班：所有班次的有效範圍
NO_NIGHTSHIFT_MIN = 8 * 60   # 08:00
NO_NIGHTSHIFT_MAX = 22 * 60  # 22:00


def resolve_partial_hours(
    partial: str,
    name: str,
    loc: Optional[str],
    type_: Optional[str],
    weekday: Optional[int] = None,
) -> Optional[tuple[str, str]]:
    """把手寫單一時間（例「1500」、「400」）對照預設班別解讀為完整範圍。

    規則（基於「無大夜班，0800-2200」）：
        - 比對手寫時間 vs 預設班別的兩個邊界
        - 替換**距離較近**的那個邊界（典型：早退 = 改 end，晚到 = 改 start）
        - 結果必須落在 0800-2200，否則回 None（讓人工 review）

    回傳 (full_range_str, side) 例如 ("0900-1500", "end")；無法解讀回 None。
    """
    if "-" in partial:
        # 已是完整範圍，不需解讀
        return None

    minutes = parse_single_time(partial)
    if minutes is None:
        return None

    default = get_shift_window(name, loc, type_, hours=None, weekday=weekday)
    if not default:
        return None

    start, end = default
    # 不允許退化成同一點
    if minutes == start or minutes == end:
        return None

    # 兩種候選
    as_end = (start, minutes) if (start < minutes <= NO_NIGHTSHIFT_MAX) else None
    as_start = (minutes, end) if (NO_NIGHTSHIFT_MIN <= minutes < end) else None

    if as_end and as_start:
        # 替換距離較近的邊界
        if abs(minutes - end) <= abs(minutes - start):
            chosen, side = as_end, "end"
        else:
            chosen, side = as_start, "start"
    elif as_end:
        chosen, side = as_end, "end"
    elif as_start:
        chosen, side = as_start, "start"
    else:
        return None

    s, e = chosen
    return (f"{s//60:02d}{s%60:02d}-{e//60:02d}{e%60:02d}", side)


# ── 取得單一班次的上班時段（分鐘） ──────────────────────────
def get_shift_window(
    name: str,
    loc: Optional[str],
    type_: Optional[str],
    hours: Optional[str] = None,
    weekday: Optional[int] = None,  # 0=Mon, 6=Sun
) -> Optional[tuple[int, int]]:
    """回傳 (start_min, end_min)；不在班 (休假/空) 回傳 None。

    weekday 用來判定志銓「平日」日晚特例（週一~五 = 17:00 起）。
    """
    if is_off_type(type_) or not type_:
        return None

    # 自訂時段優先
    if hours:
        parsed = parse_hours(hours)
        if parsed:
            return parsed

    if not loc:
        return None

    short_loc = LOC_SHORT.get(loc, loc)
    key = f"{short_loc}{type_}"

    # 志銓 平日 日晚 = 17:00–22:00（特例）
    if (
        name == "志銓"
        and key == "日晚"
        and weekday is not None
        and weekday < 5  # Mon~Fri
    ):
        return (_hm(17), _hm(22))

    return SHIFT_TIMES.get(key)


# ── 共班計算 ─────────────────────────────────────────────
def compute_coworkers(
    shifts: list[dict],
    year: int,
    month: int,
    threshold_hours: float = 5.0,
) -> dict[tuple[str, int], list[str]]:
    """回傳 {(name, day): [coworker, ...]}。

    規則：同 loc 同 day，時段重疊 ≥ threshold_hours。
    雙頭班 (no_coworker_calc=True 或 type=="雙") 跳過。
    """
    threshold_min = int(threshold_hours * 60)
    by_day: dict[int, list[dict]] = {}

    for s in shifts:
        if s.get("no_coworker_calc"):
            continue
        if s.get("type") == "雙":
            continue
        if is_off_type(s.get("type")):
            continue
        if not s.get("loc"):
            continue
        day = s["day"]
        wd = date(year, month, day).weekday() if 1 <= day <= _last_day(year, month) else None
        win = get_shift_window(s["name"], s.get("loc"), s.get("type"), s.get("hours"), wd)
        if not win:
            continue
        by_day.setdefault(day, []).append({
            "name": s["name"],
            "loc": s["loc"],
            "start": win[0],
            "end": win[1],
        })

    result: dict[tuple[str, int], list[str]] = {}
    for day, entries in by_day.items():
        for i, a in enumerate(entries):
            mates = []
            for j, b in enumerate(entries):
                if i == j or a["name"] == b["name"]:
                    continue
                if a["loc"] != b["loc"]:
                    continue
                overlap = min(a["end"], b["end"]) - max(a["start"], b["start"])
                if overlap >= threshold_min:
                    mates.append(b["name"])
            if mates:
                result[(a["name"], day)] = sorted(set(mates))
    return result


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


# ── self-test ────────────────────────────────────────────
def _selftest():
    # parse_hours
    assert parse_hours("10-18") == (600, 1080)
    assert parse_hours("1:30-2:50") == (13 * 60 + 30, 14 * 60 + 50)
    assert parse_hours("2-9") == (14 * 60, 21 * 60)
    assert parse_hours("5-9") == (17 * 60, 21 * 60)
    assert parse_hours("1-9") == (13 * 60, 21 * 60)
    assert parse_hours("13:30-14:50") == (13 * 60 + 30, 14 * 60 + 50)
    # 4 位數 / 前導 0 24h 字面
    assert parse_hours("0900-1500") == (9 * 60, 15 * 60)
    assert parse_hours("1500-2200") == (15 * 60, 22 * 60)
    assert parse_hours("9-15") == (9 * 60, 15 * 60)
    # 異常輸入
    assert parse_hours("1500") is None              # 無範圍
    assert parse_hours("2500-2700") is None         # 越界
    assert parse_hours("1500-0900") is None         # end <= start

    # parse_single_time
    assert parse_single_time("1500") == 15 * 60
    assert parse_single_time("400") == 16 * 60       # PM 簡寫
    assert parse_single_time("4") == 16 * 60         # 同上
    assert parse_single_time("0900") == 9 * 60
    assert parse_single_time("9") == 9 * 60          # 9 ≥ 8 不 +12
    assert parse_single_time("9:30") == 9 * 60 + 30
    assert parse_single_time("4:30") == 16 * 60 + 30 # PM 簡寫
    assert parse_single_time("01:30") == 60 + 30     # 前導 0 = 24h literal
    assert parse_single_time("2500") is None         # 越界

    # resolve_partial_hours
    # 可安 達早 寫 1500 → 0900-1500（早退）
    assert resolve_partial_hours("1500", "可安", "達揚", "早") == ("0900-1500", "end")
    # 阿力 達晚 寫 400 (=16:00) → 1600-2200（晚到）
    assert resolve_partial_hours("400", "阿力", "達揚", "晚") == ("1600-2200", "start")
    # 駿宇 達早 寫 18 → 0900-1800（延長到 18，比預設晚下班；end 距離 1）
    # 18 vs 0900 距離 9h，vs 1700 距離 1h → 改 end
    assert resolve_partial_hours("18", "駿宇", "達揚", "早") == ("0900-1800", "end")
    # 不合理：寫 23 → end 會超過 22:00 → 不可解讀
    assert resolve_partial_hours("23", "可安", "達揚", "早") is None
    # 已是範圍 → 不處理
    assert resolve_partial_hours("9-15", "可安", "達揚", "早") is None

    # 預設時段
    assert get_shift_window("若萍", "達揚", "早") == (540, 1020)
    assert get_shift_window("阿力", "達揚", "晚") == (840, 1320)
    assert get_shift_window("可安", "日揚", "全") == (480, 1320)  # 8-22
    assert get_shift_window("可安", "日揚", "早") == (480, 1020)  # 8-17

    # 志銓 平日（週三 = wd 2）日晚 → 17:00 起
    assert get_shift_window("志銓", "日揚", "晚", weekday=2) == (1020, 1320)
    # 志銓 週六（wd 5）日晚 → 用預設 14-22
    assert get_shift_window("志銓", "日揚", "晚", weekday=5) == (840, 1320)
    # 別人不適用此特例
    assert get_shift_window("阿發", "日揚", "晚", weekday=2) == (840, 1320)

    # 自訂時段覆蓋
    assert get_shift_window("駿宇", "達揚", "早", hours="10-18") == (600, 1080)

    # 休假
    assert get_shift_window("若萍", None, "年假") is None
    assert is_off_type("年假")
    assert is_off_type("必休")
    assert not is_off_type("早")

    # 共班：A 達早 9-17、B 達早 9-17 → 同店重疊 8h ≥ 5h
    shifts = [
        {"name": "A", "day": 1, "loc": "達揚", "type": "早"},
        {"name": "B", "day": 1, "loc": "達揚", "type": "早"},
        {"name": "C", "day": 1, "loc": "健揚", "type": "早"},  # 不同店
        {"name": "D", "day": 1, "loc": "達揚", "type": "晚"},  # 重疊 14-17 = 3h，< 5h
    ]
    cw = compute_coworkers(shifts, 2025, 4)
    assert cw[("A", 1)] == ["B"]
    assert cw[("B", 1)] == ["A"]
    assert ("D", 1) not in cw  # 重疊不夠 5h

    # 雙頭班跳過
    double_shifts = [
        {"name": "可安", "day": 24, "loc": "達揚", "type": "全", "no_coworker_calc": True},
        {"name": "其他", "day": 24, "loc": "達揚", "type": "全"},
    ]
    cw2 = compute_coworkers(double_shifts, 2025, 4)
    assert ("可安", 24) not in cw2
    assert ("其他", 24) not in cw2  # 沒有別人配對到

    print("✅ shifts.py self-test passed")


if __name__ == "__main__":
    _selftest()
