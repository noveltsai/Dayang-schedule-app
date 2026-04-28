"""
admin_panel.py — 達揚班表 本機管理介面

執行：
  cd E:\\ClaudeProject\\達揚班表
  streamlit run tools/admin_panel.py

功能：
- 月份下拉切換（讀 docs/data/index.json）
- 大表格檢視（人 × 日）
- 單格編輯（場域 / 班別 / 自訂時段）
- 「儲存到 JSON」/「儲存並推 GitHub」
"""
from __future__ import annotations

import calendar
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "docs" / "data"

LOC_OPTIONS = ["", "達揚", "日揚", "健揚"]
TYPE_OPTIONS = ["", "早", "晚", "全", "休", "必休", "年假", "補休", "雙"]
LOC_SHORT = {"達揚": "達", "日揚": "日", "健揚": "健"}
OFF_TYPES = {"休", "必休", "年假", "補休"}


# ── I/O ────────────────────────────────────────────────
def load_index() -> dict:
    p = DATA_DIR / "index.json"
    if not p.exists():
        return {"current": None, "months": []}
    return json.loads(p.read_text(encoding="utf-8"))


def list_months() -> list[tuple[int, int, str]]:
    idx = load_index()
    return [
        (m["year"], m["month"], m.get("file") or f"{m['year']}-{m['month']:02d}.json")
        for m in idx.get("months", [])
    ]


def load_month(file: str) -> dict:
    return json.loads((DATA_DIR / file).read_text(encoding="utf-8"))


def save_month(data: dict, file: str) -> None:
    data["updated_at"] = datetime.now().isoformat()
    p = DATA_DIR / file
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    idx = load_index()
    cur = (idx.get("current") or "").replace(".json", "")
    if cur == p.stem:
        (DATA_DIR / "current.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def git_push(file: str) -> None:
    rel = f"docs/data/{file}"
    subprocess.run(["git", "add", rel, "docs/data/current.json", "docs/data/index.json"],
                   cwd=PROJECT_ROOT, check=True)
    subprocess.run(["git", "commit", "-m", f"admin: edit {file}"],
                   cwd=PROJECT_ROOT, check=True)
    subprocess.run(["git", "push", "origin", "main"],
                   cwd=PROJECT_ROOT, check=True)


# ── 渲染表格 ──────────────────────────────────────────
def cell_text(s: dict | None) -> str:
    if not s:
        return ""
    t = s.get("type", "")
    if t in OFF_TYPES:
        return t
    if t == "雙" and s.get("double"):
        return "+".join(f"{LOC_SHORT.get(d['loc'], d['loc'])}{d['type']}" for d in s["double"])
    loc_short = LOC_SHORT.get(s.get("loc", ""), s.get("loc", ""))
    base = f"{loc_short}{t}"
    if s.get("hours"):
        base += f" {s['hours']}"
    return base


def build_dataframe(data: dict) -> pd.DataFrame:
    year, month = data["year"], data["month"]
    last_day = calendar.monthrange(year, month)[1]
    people = [p["name"] for p in data["people"]]
    shifts_by_key = {(s["name"], s["day"]): s for s in data["shifts"]}

    rows = []
    for name in people:
        row = {"姓名": name}
        for d in range(1, last_day + 1):
            row[f"{d}"] = cell_text(shifts_by_key.get((name, d)))
        rows.append(row)
    return pd.DataFrame(rows)


# ── 編輯邏輯 ──────────────────────────────────────────
def apply_edit(data: dict, name: str, day: int, loc: str, type_: str, hours: str, delete: bool) -> None:
    data["shifts"] = [s for s in data["shifts"] if not (s["name"] == name and s["day"] == day)]
    if delete:
        return
    if not loc and not type_:
        return
    new_shift: dict = {"name": name, "day": day}
    if loc:
        new_shift["loc"] = loc
    if type_:
        new_shift["type"] = type_
    if hours.strip():
        new_shift["hours"] = hours.strip()
    data["shifts"].append(new_shift)
    data["shifts"].sort(key=lambda s: (s["day"], s["name"]))


# ── 主程式 ────────────────────────────────────────────
def main():
    st.set_page_config(page_title="達揚班表管理", layout="wide")
    st.title("達揚班表 管理介面")

    months = list_months()
    if not months:
        st.error(f"找不到任何月份資料於 {DATA_DIR}")
        st.stop()

    # 預設選 current 那個月
    idx_data = load_index()
    cur_key = (idx_data.get("current") or "").replace(".json", "")
    default_idx = 0
    for i, (y, m, _) in enumerate(months):
        if f"{y}-{m:02d}" == cur_key:
            default_idx = i
            break

    with st.sidebar:
        st.header("月份")
        labels = [f"{y}/{m:02d}" for y, m, _ in months]
        sel_label = st.selectbox("選擇月份", labels, index=default_idx)
        sel_idx = labels.index(sel_label)
        sel_y, sel_m, sel_file = months[sel_idx]

        if st.button("🔄 重新載入") or "file" not in st.session_state or st.session_state.file != sel_file:
            st.session_state.data = load_month(sel_file)
            st.session_state.file = sel_file
            st.session_state.dirty = False

    data = st.session_state.data
    year, month = data["year"], data["month"]
    last_day = calendar.monthrange(year, month)[1]

    dirty_tag = " · ✏️ 未儲存" if st.session_state.get("dirty") else ""
    st.subheader(f"{year}/{month:02d} 班表{dirty_tag}")

    # 大表格
    df = build_dataframe(data)
    st.dataframe(df, use_container_width=True, height=560, hide_index=True)

    # 編輯表單
    st.divider()
    st.subheader("✏️ 編輯單一格")

    people = [p["name"] for p in data["people"]]
    shifts_by_key = {(s["name"], s["day"]): s for s in data["shifts"]}

    col1, col2 = st.columns([1, 1])
    with col1:
        edit_name = st.selectbox("姓名", people, key="edit_name")
    with col2:
        edit_day = st.number_input("日期", min_value=1, max_value=last_day, value=1, step=1, key="edit_day")

    existing = shifts_by_key.get((edit_name, edit_day))
    st.caption(f"目前內容：**{cell_text(existing) or '（空）'}**")

    with st.form("edit_form"):
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            cur_loc = existing.get("loc", "") if existing else ""
            new_loc = st.selectbox("場域", LOC_OPTIONS,
                                   index=LOC_OPTIONS.index(cur_loc) if cur_loc in LOC_OPTIONS else 0)
        with col_b:
            cur_type = existing.get("type", "") if existing else ""
            new_type = st.selectbox("班別", TYPE_OPTIONS,
                                    index=TYPE_OPTIONS.index(cur_type) if cur_type in TYPE_OPTIONS else 0)
        with col_c:
            cur_hours = existing.get("hours", "") if existing else ""
            new_hours = st.text_input("自訂時段（如 10-18）", value=cur_hours)

        delete_shift = st.checkbox("刪除這格（恢復未排班）", value=False)

        submitted = st.form_submit_button("✓ 套用變更（暫存）", type="primary")
        if submitted:
            apply_edit(data, edit_name, edit_day, new_loc, new_type, new_hours, delete_shift)
            st.session_state.data = data
            st.session_state.dirty = True
            st.success(f"已更新 {edit_name} {month}/{edit_day}（記得下方按 儲存）")
            st.rerun()

    # 儲存 + 推 push
    st.divider()
    col_save, col_push, col_reset = st.columns(3)
    with col_save:
        if st.button("💾 儲存到 JSON", use_container_width=True):
            save_month(st.session_state.data, st.session_state.file)
            st.session_state.dirty = False
            st.success(f"✅ 已寫入 {st.session_state.file}")
    with col_push:
        if st.button("🚀 儲存並推 GitHub", use_container_width=True, type="primary"):
            save_month(st.session_state.data, st.session_state.file)
            try:
                git_push(st.session_state.file)
                st.session_state.dirty = False
                st.success("✅ 已 push 到 GitHub（1-2 分鐘後生效）")
            except subprocess.CalledProcessError as e:
                st.error(f"git 失敗：{e}")
    with col_reset:
        if st.button("⟲ 放棄變更（重載）", use_container_width=True):
            st.session_state.data = load_month(st.session_state.file)
            st.session_state.dirty = False
            st.rerun()


if __name__ == "__main__":
    main()
