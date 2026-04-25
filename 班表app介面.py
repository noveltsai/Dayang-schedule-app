import streamlit as st
import pandas as pd
import requests
from datetime import datetime
import json
import io
import os
import calendar
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 設定
# ==========================================
st.set_page_config(page_title="達揚連鎖藥局班表系統", layout="centered", page_icon="🏥")

FONT_FILE = "NotoSansTC-Regular.ttf"

# schedule_data.json 的 Google Drive File ID（需設為任何人可檢視）
# 首次由 admin_upload.py 建立後，把 ID 填入 Streamlit Secrets：GDRIVE_FILE_ID = "xxx"
GDRIVE_FILE_ID = st.secrets.get("GDRIVE_FILE_ID", "1dmpUvd-2waykkbF_BfYLKDqSllRTOkSM")

# ==========================================
# 讀取雲端班表
# ==========================================

def load_schedule() -> dict | None:
    """從 Google Drive 公開連結下載 schedule_data.json"""
    try:
        url = f"https://drive.google.com/uc?id={GDRIVE_FILE_ID}&export=download"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        st.error(f"讀取班表失敗：{e}")
        return None

# ==========================================
# 繪圖
# ==========================================

def generate_calendar_image(user_df, year, month, name):
    HEADER_H = 150
    CELL_H = 135
    img_w = 750
    cell_w = img_w // 7

    cal = calendar.monthcalendar(year, month)
    img_h = HEADER_H + CELL_H * len(cal)

    img = Image.new("RGB", (img_w, img_h), color="#FFFFFF")
    draw = ImageDraw.Draw(img)

    if os.path.exists(FONT_FILE):
        f_title = ImageFont.truetype(FONT_FILE, 32)
        f_header = ImageFont.truetype(FONT_FILE, 22)
        f_day = ImageFont.truetype(FONT_FILE, 20)
        f_shift = ImageFont.truetype(FONT_FILE, 17)
    else:
        f_title = f_header = f_day = f_shift = ImageFont.load_default()

    draw.text((30, 25), f"達揚連鎖 - {year}年{month}月 {name} 班表", fill="#000000", font=f_title)
    for i, d in enumerate(["一", "二", "三", "四", "五", "六", "日"]):
        draw.text((i * cell_w + 38, 95), d, fill="#FF4500" if i >= 5 else "#000000", font=f_header)

    shift_dict = {row["day"]: row["shift"] for _, row in user_df.iterrows()}

    for row_idx, week in enumerate(cal):
        for col_idx, day in enumerate(week):
            if day == 0:
                continue
            x, y = col_idx * cell_w, HEADER_H + row_idx * CELL_H
            draw.rectangle([x, y, x + cell_w, y + CELL_H], outline="#EEEEEE")
            draw.text((x + 8, y + 8), str(day), fill="#FF4500" if col_idx >= 5 else "#666666", font=f_day)
            if day in shift_dict:
                s = shift_dict[day]
                color = "#FF8C00" if "日揚" in s else ("#2E8B57" if "健揚" in s else "#1E90FF")
                draw.text((x + 12, y + 40), s[:2], fill=color, font=f_shift)
                draw.text((x + 12, y + 65), s[2:], fill="#333333", font=f_shift)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

# ==========================================
# 時間計算
# ==========================================

def get_shift_times(name, loc, shift_type, y, m, d):
    if shift_type == "早":
        sh, eh = (8, 17) if loc == "日揚" else (9, 17)
    elif shift_type == "晚":
        sh, eh = (17, 22) if name == "志銓" and loc == "日揚" else (14, 22)
    elif shift_type == "全":
        sh, eh = (8, 22) if loc == "日揚" else (9, 22)
    else:
        sh, eh = (0, 0)
    start = pd.to_datetime(f"{y}-{m:02d}-{d:02d} {sh:02d}:00")
    end = pd.to_datetime(f"{y}-{m:02d}-{d:02d} {eh:02d}:00")
    return start, end

# ==========================================
# 主介面
# ==========================================

st.title("🏥 達揚藥局 個人班表系統")

stored = load_schedule()
if not stored:
    st.warning("⚠️ 無法取得班表資料，請聯絡管理員。")
    st.stop()

st.info(f"📅 目前版本：{stored['year']} 年 {stored['month']} 月班表")

loc_map = {"達": "達揚", "日": "日揚", "健": "健揚"}
rows = []
for row in stored["data"]:
    f_loc = loc_map.get(row["loc"], "未知")
    st_t, en_t = get_shift_times(row["name"], f_loc, row["type"], stored["year"], stored["month"], row["day"])
    rows.append({"name": row["name"], "day": row["day"], "start": st_t, "end": en_t,
                 "shift": f"{f_loc}{row['type']}班"})
full_df = pd.DataFrame(rows)

# 下拉選單：依班表順序 + 編號
stored_order = stored.get("name_order", sorted(full_df["name"].unique().tolist()))
available = set(full_df["name"].unique())
ordered = [n for n in stored_order if n in available]
for n in sorted(available - set(ordered)):
    ordered.append(n)

options = [f"{i+1}. {name}" for i, name in enumerate(ordered)]
sel = st.selectbox("👤 選擇您的姓名", options=options)
sel_name = sel.split(". ", 1)[1]

my_df = full_df[full_df["name"] == sel_name].sort_values("day")

with st.expander("🔍 本月共班夥伴 (重疊滿5小時)"):
    for _, my_s in my_df.iterrows():
        others = full_df[(full_df["day"] == my_s["day"]) & (full_df["name"] != sel_name)]
        partners = [op["name"] for _, op in others.iterrows()
                    if (min(my_s["end"], op["end"]) - max(my_s["start"], op["start"])).total_seconds() / 3600 >= 5]
        st.write(f"**{my_s['day']}日**：{', '.join(partners) if partners else '獨自值班'}")

if st.button("🖼️ 產生我的手機班表圖"):
    with st.spinner("產生中..."):
        st.image(generate_calendar_image(my_df, stored["year"], stored["month"], sel_name), width=375)

st.markdown("---")
st.caption(f"© {datetime.now().year} 達揚連鎖藥局 | 志銓")
