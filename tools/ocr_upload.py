"""
ocr_upload.py — 達揚班表 OCR 上傳工具（本機管理員專用）

流程：
  1. 從檔名/旗標讀年月
  2. 呼叫 Gemini Vision 辨識班表 JPG
  3. Terminal review TUI：人名 → 不確定格 → 自訂時段 → 雙頭班
  4. 寫 docs/data/YYYY-MM.json（新 schema），刷新 current.json
  5. 可選 git add/commit/push

用法：
  python tools/ocr_upload.py path/to/2026-05.jpg
  python tools/ocr_upload.py path/to/LINE_ALBUM_2026年05月_xxx.jpg
  python tools/ocr_upload.py path/to/img.jpg --year 2026 --month 5
  python tools/ocr_upload.py path/to/img.jpg --push
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 專案根目錄（tools/ 的上一層）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "docs" / "data"

# 員工特殊規則（plan 中已確認）
PEOPLE_RULES = {
    # name: (ghost_when_blank, loc_lock)
    "蕭藥師": (True, None),
    "旻翰": (True, "日揚"),
    "宇庭": (True, "達揚-早"),
}

# 班別 / 場域對照
LOC_FULL = {"達": "達揚", "日": "日揚", "健": "健揚"}
VALID_TYPES = {"早", "晚", "全", "休", "必休", "年假", "補休", "雙"}
OFF_TYPES = {"休", "必休", "年假", "補休"}


# ── 檔名解析 ─────────────────────────────────────────────
def parse_year_month_from_filename(filename: str) -> tuple[int | None, int | None]:
    """從檔名抓年月。支援多種格式：
        2026-05.jpg / 2026_05.jpg / 202605.jpg
        LINE_ALBUM_2026年05月_xxx.jpg / 2026年5月.jpg
        202605__Dayang_Schedule.jpg
    """
    # 「YYYY年MM月」
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 「YYYY?月」（年份後直接接月份 + 月字，例：20265月）
    m = re.search(r"(20\d{2})\s*(\d{1,2})\s*月", filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    # YYYY[-_]MM
    m = re.search(r"(20\d{2})[-_](\d{1,2})(?!\d)", filename)
    if m:
        return int(m.group(1)), int(m.group(2))
    # 連寫 YYYYMM（年份 20xx + 兩位月份）
    m = re.search(r"(20\d{2})(\d{2})(?!\d)", filename)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return y, mo
    return None, None


# ── Gemini 載入 ──────────────────────────────────────────
def load_gemini_key() -> str:
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "GEMINI_API_KEY":
                v = v.strip().strip('"').strip("'")
                if v:
                    return v
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    return input("請輸入 Gemini API Key：").strip()


def ocr_schedule(img_path: Path, year: int, month: int, model_override: str | None = None) -> list[dict]:
    """呼叫 Gemini Vision 辨識，回傳 raw shifts list。"""
    import google.generativeai as genai
    from PIL import Image

    api_key = load_gemini_key()
    genai.configure(api_key=api_key)
    # 模型優先序：--model 參數 > GEMINI_MODEL 環境變數 > 預設 3.1 Pro
    model_name = (
        model_override
        or os.environ.get("GEMINI_MODEL")
        or "gemini-3.1-pro-preview"
    )
    model = genai.GenerativeModel(model_name)

    img = Image.open(img_path)
    prompt = build_prompt(year, month)

    print(f"📷 呼叫 Gemini ({model_name})…（這張可能要 1-3 分鐘）")
    response = model.generate_content(
        [prompt, img],
        request_options={"timeout": 1200},  # 20 分鐘（Pro 跑大圖細表常需 8-10 分鐘）
    )
    text = response.text.strip()

    # 抓 [...] 區段
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        raise RuntimeError(f"Gemini 回傳無法解析：\n{text}")
    return json.loads(m.group(0))


def build_prompt(year: int, month: int) -> str:
    return f"""這是一張達揚連鎖藥局的 {year} 年 {month} 月排班表。
請辨識所有員工每一格的班次資訊，回傳乾淨的 JSON Array（不要含 ```json 標記）。

格式：
[
  {{"name":"志銓","day":1,"loc":"健","type":"晚"}},
  {{"name":"若萍","day":8,"type":"年假"}},
  {{"name":"駿宇","day":1,"loc":"達","hours":"10-18"}},
  {{"name":"可安","day":24,"loc":"達","type":"雙",
   "double":[{{"loc":"達","type":"早晚"}},{{"loc":"健","type":"晚"}}],
   "no_coworker_calc":true}},
  {{"name":"阿發","day":15,"loc":"日","type":"早","uncertain":true,"note":"手寫模糊"}}
]

欄位規則：
- name: 員工姓名（中文）
- day: 1-31 整數
- loc: "達" / "日" / "健"，**休假類無此欄位**
- type:
    工作班 → "早" / "晚" / "全"
    休假類 → "休" / "必休" / "年假" / "補休"
    雙頭班 → "雙"（同時須附 double 陣列）
- hours: 選填，**手寫自訂時段**才填（如駿宇、家棋常見）。
    **照實抄寫，不要自己補完整。** 規則：
    - 看到完整範圍：原樣保留。例：「10-18」、「1:30-2:50」、「9-15」、「0900-1500」
    - 看到單一時間（早退或晚到）：原樣保留單一值，例：「1500」、「400」、「16」、「2030」。
      Python 端會根據該員工那天的「預設班別」自動推算成完整範圍：
        * 可安 那天是達早（0900-1700），手寫 1500 → 系統推為 09:00-15:00（早退）
        * 阿力 那天是達晚（1400-2200），手寫 400（=16:00）→ 系統推為 16:00-22:00（晚到）
      因此**你必須仍然提供 type 欄位**（早/晚/全），讓 Python 知道預設班別。
- double: 雙頭班才有，例如可安某日「達早晚 + 健晚」就拆成兩段
- no_coworker_calc: 雙頭班務必設 true（共班計算要跳過）
- uncertain: 你不確定時設 true，並用 note 說明
- note: 任何補充（手寫看不清、印錯、可能塗改等）

特別注意：
- **駿宇**（常規兼職、通常排在班表最末列、下方無人）：他的手寫時段常**寫得很大、超出單一格子往下延伸**。
  請忽略印刷格子線，**以駿宇姓名所在的橫列為基準，把該列每個日期欄位下方一直延伸到「下一位員工列的起點」之前的所有手寫文字，全部視為駿宇的內容**。
  注意：此規則**只適用於駿宇**，其他人嚴格依格子辨識，不要把下一格的內容當成上一格的延伸。
- **家棋**（健班常規）：手寫時段（如「健 1-9」=「13:00-21:00」）通常**寫在格子內**，請依該格內容辨識，不要往下延伸。
- 不要遺漏休假格（休 / 必休 / 年假 / 補休 全部要列），店長要算時數。
- 按班表「人 × 日」順序輸出，同一人的班次連續列出。"""


# ── Review TUI ────────────────────────────────────────────
def review_names(shifts: list[dict]) -> list[str]:
    """確認名單順序，回傳修正後的 name_order。"""
    seen: list[str] = []
    for s in shifts:
        n = s.get("name", "").strip()
        if n and n not in seen:
            seen.append(n)

    print("\n── 辨識到的員工名單（依班表順序）──")
    corrected = []
    for i, name in enumerate(seen):
        fix = input(f"  {i+1:>2}. {name}（Enter 保留，輸入新名修正）: ").strip()
        corrected.append(fix or name)
    return seen, corrected


def apply_name_map(shifts: list[dict], old: list[str], new: list[str]) -> list[dict]:
    mp = dict(zip(old, new))
    out = []
    for s in shifts:
        s = dict(s)
        s["name"] = mp.get(s["name"], s["name"])
        out.append(s)
    return out


def review_uncertain(shifts: list[dict]) -> list[dict]:
    """逐筆 review AI 標 uncertain 的格子。"""
    pending = [s for s in shifts if s.get("uncertain")]
    if not pending:
        return shifts
    print(f"\n── AI 不確定的格子（{len(pending)} 筆）──")
    for s in pending:
        note = s.get("note", "")
        guess = format_shift_inline(s)
        print(f"  {s['name']} {s['day']}日：AI 猜「{guess}」 {f'({note})' if note else ''}")
        ans = input("    Enter 保留 / d 刪除 / 輸入新值（格式：loc type 或 type，例 達早 / 健全 / 年假）: ").strip()
        if ans == "":
            s.pop("uncertain", None)
            s.pop("note", None)
            continue
        if ans.lower() == "d":
            shifts.remove(s)
            continue
        parsed = parse_shift_input(ans)
        if parsed:
            s.update(parsed)
            s.pop("uncertain", None)
            s.pop("note", None)
        else:
            print(f"    無法解析「{ans}」，保留原值")
    return shifts


def review_custom_hours(shifts: list[dict]) -> list[dict]:
    """檢查 hours：單一時間自動依預設班別推算；完整範圍直接驗證。"""
    pending = [s for s in shifts if s.get("hours")]
    if not pending:
        return shifts
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from shifts import parse_hours, resolve_partial_hours, LOC_FULL

    print(f"\n── 自訂時段（{len(pending)} 筆）──")
    print("  說明：單一時間（如 1500、400）會依預設班別自動推算為完整範圍")
    print()

    for s in pending:
        loc_short = s.get("loc", "")
        loc_full = LOC_FULL.get(loc_short, loc_short)
        type_ = s.get("type", "")
        original = s["hours"]

        # 嘗試自動推算（只對單一時間生效）
        resolved = resolve_partial_hours(original, s["name"], loc_full, type_)
        if resolved:
            full_range, side = resolved
            side_label = "提早下班（改 end）" if side == "end" else "晚到上班（改 start）"
            print(f"  {s['name']} {s['day']}日 {loc_short}{type_}：手寫「{original}」 → 推算 {full_range[:4]}-{full_range[5:]} ({side_label})")
            ans = input("    Enter 接受推算 / 輸入完整範圍覆寫（如 0900-1500） / k 強制保留原字串: ").strip()
            if ans == "":
                s["hours"] = full_range
                continue
            if ans.lower() == "k":
                continue  # 保留原 "1500" 字串，前端會用 type 預設時段
            # 用戶覆寫
            parsed = parse_hours(ans)
            if parsed:
                s["hours"] = ans
                start_h, start_m = divmod(parsed[0], 60)
                end_h, end_m = divmod(parsed[1], 60)
                print(f"    ✓ 解析為 {start_h:02d}:{start_m:02d}-{end_h:02d}:{end_m:02d}")
            else:
                print(f"    ✗ 無法解析「{ans}」，仍套用推算 {full_range}")
                s["hours"] = full_range
        else:
            # 無法自動推算（已是範圍 or 越界）→ 走驗證流程
            while True:
                print(f"  {s['name']} {s['day']}日 {loc_short}{type_}：時段「{original}」")
                ans = input("    Enter 保留 / 輸入新時段（如 10-18 / 0900-1500）: ").strip()
                candidate = ans if ans else original
                parsed = parse_hours(candidate)
                if parsed:
                    start_h, start_m = divmod(parsed[0], 60)
                    end_h, end_m = divmod(parsed[1], 60)
                    print(f"    ✓ 解析為 {start_h:02d}:{start_m:02d}-{end_h:02d}:{end_m:02d}")
                    if ans:
                        s["hours"] = ans
                    break
                else:
                    print(f"    ✗ 無法解析「{candidate}」")
                    retry = input("      重輸？ y=重輸 / Enter=就這樣存（前端會顯示原字串）: ").strip().lower()
                    if retry != "y":
                        if ans:
                            s["hours"] = ans
                        break
    return shifts


def final_confirm(shifts: list[dict]) -> list[dict]:
    """結尾總覽：列出所有自訂時段 + 雙頭班 + 休假，最後一次校對機會。"""
    while True:
        print("\n── 最終確認 ──")
        notable = []
        for i, s in enumerate(shifts):
            if s.get("hours") or s.get("type") == "雙" or s.get("type") in OFF_TYPES:
                tag = (
                    s.get("hours") and f"hours={s['hours']}"
                    or s.get("type") == "雙" and "雙頭班"
                    or f"休假={s['type']}"
                )
                loc = s.get("loc", "")
                notable.append((i, s, f"  [{len(notable)+1:>2}] {s['name']} {s['day']}日 {loc} → {tag}"))

        if not notable:
            print("  （無自訂時段 / 雙頭班 / 休假需要再確認）")
            return shifts

        for _, _, line in notable:
            print(line)

        ans = input(
            "\n  Enter 全部 OK / 輸入編號修改該筆 hours（例：1）/ q 中斷不寫檔: "
        ).strip().lower()
        if ans == "":
            return shifts
        if ans == "q":
            print("⚠️  中斷，未寫檔")
            sys.exit(0)
        if ans.isdigit():
            idx = int(ans) - 1
            if 0 <= idx < len(notable):
                _, target, _ = notable[idx]
                if target.get("hours"):
                    new = input(f"    新 hours（目前「{target['hours']}」，Enter 取消）: ").strip()
                    if new:
                        target["hours"] = new
                elif target.get("type") in OFF_TYPES:
                    new = input(f"    新休假類型（休/必休/年假/補休，Enter 取消）: ").strip()
                    if new in OFF_TYPES:
                        target["type"] = new
                else:
                    print("    雙頭班請手改 JSON 或用 admin_panel")
                continue
        print("    輸入無效")


def auto_resolve_hours(shifts: list[dict]) -> list[dict]:
    """所有單一時間 hours 自動套用 resolve_partial_hours 推算結果。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from shifts import resolve_partial_hours, LOC_FULL as SH_LOC_FULL

    for s in shifts:
        h = s.get("hours")
        if not h or "-" in h:
            continue
        loc_full = SH_LOC_FULL.get(s.get("loc", ""), s.get("loc"))
        resolved = resolve_partial_hours(h, s["name"], loc_full, s.get("type"))
        if resolved:
            s["hours"] = resolved[0]
            s["_partial_resolved"] = resolved[1]  # 留給 review 顯示用
    return shifts


def auto_mark_double(shifts: list[dict]) -> list[dict]:
    """所有 type=雙 一律標 no_coworker_calc=True。"""
    for s in shifts:
        if s.get("type") == "雙":
            s["no_coworker_calc"] = True
    return shifts


def review_double_shifts(shifts: list[dict]) -> list[dict]:
    """確認雙頭班無誤。"""
    pending = [s for s in shifts if s.get("type") == "雙"]
    if not pending:
        return shifts
    print(f"\n── 雙頭班（{len(pending)} 筆）──")
    for s in pending:
        dbl = s.get("double", [])
        desc = " + ".join(f"{d.get('loc','?')}{d.get('type','?')}" for d in dbl)
        print(f"  {s['name']} {s['day']}日：{desc}")
        s["no_coworker_calc"] = True
    return shifts


def format_shift_inline(s: dict) -> str:
    if s.get("type") in OFF_TYPES:
        return s["type"]
    loc = s.get("loc", "")
    return f"{loc}{s.get('type','')}"


def parse_shift_input(text: str) -> dict | None:
    """解析 review 時的輸入：'達早' / '健全' / '年假' / '休'。"""
    text = text.strip()
    if text in OFF_TYPES:
        return {"type": text, "loc": None}
    m = re.match(r"^([達日健])([早晚全雙])$", text)
    if m:
        return {"loc": m.group(1), "type": m.group(2)}
    return None


# ── Schema 轉換 ──────────────────────────────────────────
def to_full_loc(short_or_full: str | None) -> str | None:
    if not short_or_full:
        return None
    return LOC_FULL.get(short_or_full, short_or_full)


def build_full_json(
    year: int,
    month: int,
    shifts_raw: list[dict],
    name_order: list[str],
) -> dict:
    """轉成前端 schema：{year, month, updated_at, people[], shifts[]}。"""
    people = []
    for n in name_order:
        ghost, lock = PEOPLE_RULES.get(n, (False, None))
        people.append({"name": n, "ghost_when_blank": ghost, "loc_lock": lock})

    shifts_clean = []
    for s in shifts_raw:
        out = {"name": s["name"], "day": int(s["day"])}
        loc = to_full_loc(s.get("loc"))
        if loc:
            out["loc"] = loc
        if s.get("type"):
            out["type"] = s["type"]
        if s.get("hours"):
            out["hours"] = s["hours"]
        if s.get("double"):
            out["double"] = [
                {"loc": to_full_loc(d.get("loc")), "type": d.get("type")}
                for d in s["double"]
            ]
        if s.get("no_coworker_calc"):
            out["no_coworker_calc"] = True
        shifts_clean.append(out)

    return {
        "year": year,
        "month": month,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "people": people,
        "shifts": shifts_clean,
    }


# ── Git ──────────────────────────────────────────────────
def git_push(year: int, month: int) -> None:
    fname = f"{year}-{month:02d}.json"
    print(f"\n📤 git add / commit / push…")
    subprocess.run(["git", "add", "docs/data/"], cwd=PROJECT_ROOT, check=True)
    msg = f"schedule: {year}-{month:02d}"
    subprocess.run(["git", "commit", "-m", msg], cwd=PROJECT_ROOT, check=True)
    subprocess.run(["git", "push"], cwd=PROJECT_ROOT, check=True)
    print(f"✅ 已推送 {fname}")


# ── Main ─────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description="達揚班表 OCR 上傳工具")
    p.add_argument("image", help="班表 JPG 路徑")
    p.add_argument("--year", type=int, help="覆寫年（不指定則從檔名抓）")
    p.add_argument("--month", type=int, help="覆寫月（不指定則從檔名抓）")
    p.add_argument("--no-review", action="store_true", help="跳過所有 review")
    p.add_argument("--push", action="store_true", help="完成後 git commit + push")
    p.add_argument("--dry-run", action="store_true", help="不寫檔，只印結果")
    p.add_argument("--model", help="覆寫 Gemini 模型名（如 gemini-3-flash-preview）")
    args = p.parse_args()

    img_path = Path(args.image)
    if not img_path.exists():
        print(f"❌ 找不到圖片：{img_path}")
        return 1

    # 年月
    year, month = args.year, args.month
    if year is None or month is None:
        y2, m2 = parse_year_month_from_filename(img_path.name)
        year = year or y2
        month = month or m2
    if year is None or month is None:
        print("❌ 無法從檔名抓出年月，請用 --year --month 指定")
        return 1
    print(f"📅 年月：{year}-{month:02d}")

    # OCR
    try:
        raw = ocr_schedule(img_path, year, month, model_override=args.model)
    except Exception as e:
        print(f"❌ Gemini 辨識失敗：{e}")
        return 1
    print(f"✅ 辨識完成，共 {len(raw)} 筆班次")

    # 自動處理（不論是否 review，都先跑這些）
    raw = auto_resolve_hours(raw)
    raw = auto_mark_double(raw)

    # Review
    if not args.no_review:
        old, new = review_names(raw)
        raw = apply_name_map(raw, old, new)
        name_order = new
        raw = review_uncertain(raw)
        raw = review_custom_hours(raw)
        raw = review_double_shifts(raw)
        raw = final_confirm(raw)
    else:
        name_order = []
        for s in raw:
            n = s["name"]
            if n not in name_order:
                name_order.append(n)
        print("⚡ --no-review：跳過所有人工確認，直接寫檔（hours 已自動推算）")

    full = build_full_json(year, month, raw, name_order)

    if args.dry_run:
        print("\n── DRY RUN 預覽 ──")
        print(json.dumps(full, ensure_ascii=False, indent=2)[:2000])
        return 0

    # 寫檔
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"{year}-{month:02d}.json"
    out_path.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copyfile(out_path, DATA_DIR / "current.json")
    rebuild_index(DATA_DIR, year, month)
    print(f"💾 寫入 {out_path.relative_to(PROJECT_ROOT)} + current.json + index.json")

    if args.push:
        try:
            git_push(year, month)
        except subprocess.CalledProcessError as e:
            print(f"⚠️ git 操作失敗：{e}（檔案已存好，請手動 push）")
            return 1

    return 0


def rebuild_index(data_dir: Path, year: int, month: int) -> None:
    """掃 docs/data/ 下所有 YYYY-MM.json，重建 index.json（current 指向剛寫入的月份）。
    保留既有 note 欄位（人工標註不會被覆蓋）。"""
    existing_notes: dict[str, str] = {}
    idx_path = data_dir / "index.json"
    if idx_path.exists():
        try:
            old = json.loads(idx_path.read_text(encoding="utf-8"))
            for m in old.get("months", []):
                if m.get("note"):
                    key = f"{m['year']}-{m['month']:02d}"
                    existing_notes[key] = m["note"]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    months = []
    for p in sorted(data_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].json")):
        try:
            y, m = p.stem.split("-")
            entry = {"year": int(y), "month": int(m), "file": p.name}
            note = existing_notes.get(p.stem)
            if note:
                entry["note"] = note
            months.append(entry)
        except ValueError:
            continue
    index = {
        "current": f"{year}-{month:02d}",
        "months": months,
    }
    idx_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    sys.exit(main())
