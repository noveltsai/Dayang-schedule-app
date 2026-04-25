import streamlit as st
import pandas as pd
import google.generativeai as genai
from datetime import datetime
import json
import io
import re
import os
import calendar
from PIL import Image, ImageDraw, ImageFont
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

# ==========================================
# 1. 核心設定
# ==========================================
st.set_page_config(page_title="達揚連鎖藥局班表系統", layout="centered", page_icon="🏥")

FOLDER_ID = "1UiYKHzW6tJq6gjML56nktUh5IugLqeaS"
FONT_FILE = "NotoSansTC-Regular.ttf"
JSON_FILE_NAME = "schedule_data.json"

# ==========================================
# 2. Session State 初始化
# ==========================================
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False
if "pending_content" not in st.session_state:
    # 暫存 AI 辨識結果，等管理員確認名單後才存 Drive
    st.session_state.pending_content = None

# ==========================================
# 3. Sidebar：API 狀態 + 管理員登入
# ==========================================

# Gemini
try:
    api_key = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=api_key)
    st.sidebar.success("✅ Gemini AI 連線成功")
except Exception:
    st.sidebar.warning("⚠️ 未偵測到 Gemini 金鑰")
    api_key = st.sidebar.text_input("🔑 手動輸入 API Key", type="password")
    if api_key:
        genai.configure(api_key=api_key)

# GDrive secret 存在性
if "gcp_service_account" in st.secrets:
    st.sidebar.success("✅ Google Drive 金鑰已設定")
else:
    st.sidebar.error("❌ Google Drive 金鑰未設定")

st.sidebar.divider()

# --- 管理員登入（密碼存在 ADMIN_PASSWORD secret）---
if not st.session_state.admin_logged_in:
    st.sidebar.subheader("🔐 管理員登入")
    pwd_input = st.sidebar.text_input("密碼", type="password", key="admin_pwd")
    if st.sidebar.button("登入"):
        correct = st.secrets.get("ADMIN_PASSWORD", "")
        if pwd_input and pwd_input == correct:
            st.session_state.admin_logged_in = True
            st.rerun()
        else:
            st.sidebar.error("密碼錯誤")
else:
    st.sidebar.success("🛠️ 管理員已登入")
    if st.sidebar.button("登出"):
        st.session_state.admin_logged_in = False
        st.session_state.pending_content = None
        st.rerun()

is_admin = st.session_state.admin_logged_in

# ==========================================
# 4. Google Drive 工具函數
# ==========================================

def get_gdrive_service():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        if "private_key" in creds_dict:
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        creds = service_account.Credentials.from_service_account_info(creds_dict)
        scoped_creds = creds.with_scopes(['https://www.googleapis.com/auth/drive'])
        return build('drive', 'v3', credentials=scoped_creds)
    except KeyError:
        return None
    except Exception as e:
        st.sidebar.error(f"GCP 連線錯誤: {e}")
        return None


def load_from_drive():
    service = get_gdrive_service()
    if not service:
        return None
    try:
        query = f"name = '{JSON_FILE_NAME}' and '{FOLDER_ID}' in parents and trashed = false"
        results = service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])
        if not files:
            return None
        request = service.files().get_media(fileId=files[0]['id'])
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return json.loads(fh.getvalue().decode('utf-8'))
    except Exception as e:
        st.error(f"讀取雲端資料失敗: {e}")
        return None


def save_to_drive(content):
    service = get_gdrive_service()
    if not service:
        st.warning("⚠️ 尚未設定 Google Drive 金鑰！目前僅顯示辨識結果（未存檔）：")
        st.json(content)
        return False
    try:
        json_bytes = json.dumps(content, ensure_ascii=False, indent=4).encode('utf-8')
        media = MediaIoBaseUpload(io.BytesIO(json_bytes), mimetype='application/json', resumable=True)
        query = f"name = '{JSON_FILE_NAME}' and '{FOLDER_ID}' in parents and trashed = false"
        results = service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])
        if files:
            service.files().update(fileId=files[0]['id'], media_body=media).execute()
        else:
            file_metadata = {'name': JSON_FILE_NAME, 'parents': [FOLDER_ID]}
            service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        st.success("✅ 資料已同步至雲端！")
        return True
    except Exception as e:
        st.error(f"存檔至雲端失敗: {e}")
        return False

# ==========================================
# 5. 繪圖函數
# ==========================================

def generate_calendar_image(user_df, year, month, name):
    HEADER_H = 150
    CELL_H = 135
    img_w = 750
    cell_w = img_w // 7

    cal = calendar.monthcalendar(year, month)
    img_h = HEADER_H + CELL_H * len(cal)

    img = Image.new('RGB', (img_w, img_h), color='#FFFFFF')
    draw = ImageDraw.Draw(img)

    if os.path.exists(FONT_FILE):
        f_title = ImageFont.truetype(FONT_FILE, 32)
        f_header = ImageFont.truetype(FONT_FILE, 22)
        f_day = ImageFont.truetype(FONT_FILE, 20)
        f_shift = ImageFont.truetype(FONT_FILE, 17)
    else:
        f_title = f_header = f_day = f_shift = ImageFont.load_default()

    draw.text((30, 25), f"達揚連鎖 - {year}年{month}月 {name} 班表", fill="#000000", font=f_title)
    days_of_week = ["一", "二", "三", "四", "五", "六", "日"]
    for i, d_name in enumerate(days_of_week):
        color = "#FF4500" if i >= 5 else "#000000"
        draw.text((i * cell_w + 38, 95), d_name, fill=color, font=f_header)

    shift_dict = {row['day']: row['shift'] for _, row in user_df.iterrows()}

    for row_idx, week in enumerate(cal):
        for col_idx, day in enumerate(week):
            if day == 0:
                continue
            x = col_idx * cell_w
            y = HEADER_H + row_idx * CELL_H
            draw.rectangle([x, y, x + cell_w, y + CELL_H], outline="#EEEEEE")
            color_d = "#FF4500" if col_idx >= 5 else "#666666"
            draw.text((x + 8, y + 8), str(day), fill=color_d, font=f_day)
            if day in shift_dict:
                s_name = shift_dict[day]
                color_s = "#1E90FF"
                if "日揚" in s_name:
                    color_s = "#FF8C00"
                elif "健揚" in s_name:
                    color_s = "#2E8B57"
                loc, t = s_name[:2], s_name[2:]
                draw.text((x + 12, y + 40), loc, fill=color_s, font=f_shift)
                draw.text((x + 12, y + 65), t, fill="#333333", font=f_shift)

    img_output = io.BytesIO()
    img.save(img_output, format='PNG')
    return img_output.getvalue()

# ==========================================
# 6. 時間計算
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
    start_dt = pd.to_datetime(f"{y}-{m:02d}-{d:02d} {sh:02d}:00")
    end_dt = pd.to_datetime(f"{y}-{m:02d}-{d:02d} {eh:02d}:00")
    return start_dt, end_dt

# ==========================================
# 7. 工具：從 AI data 擷取初始名字順序
# ==========================================

def extract_name_order(parsed_data: list) -> list:
    """依 AI 辨識結果的出現順序（班表由上到下）產生不重複名字清單。"""
    seen = []
    for row in parsed_data:
        name = row.get("name", "").strip()
        if name and name not in seen:
            seen.append(name)
    return seen

# ==========================================
# 8. 主程式介面
# ==========================================

if is_admin:
    # ── 管理員介面 ──────────────────────────────────────────────────
    st.header("📤 管理員：上傳新班表")

    # Step 1：若已有 pending 結果，進入確認名單步驟
    if st.session_state.pending_content is not None:
        pending = st.session_state.pending_content
        st.subheader("📋 Step 2：確認辨識名單後存檔")
        st.info(f"AI 辨識班表：{pending['year']} 年 {pending['month']} 月，共 {len(pending['data'])} 筆記錄")

        st.write("**請確認以下辨識到的員工名單（可修改錯字）：**")
        st.caption("名單順序 = 班表由上到下的順序，也是下拉選單的排列順序。")

        # 讓管理員逐筆確認/修正名字，用 text_input 做成可編輯清單
        original_order = pending.get("name_order", [])
        corrected_names = {}  # old_name → new_name

        cols = st.columns(2)
        for i, old_name in enumerate(original_order):
            col = cols[i % 2]
            new_name = col.text_input(
                f"{i+1}. 員工 {i+1}",
                value=old_name,
                key=f"name_edit_{i}"
            )
            corrected_names[old_name] = new_name.strip() if new_name.strip() else old_name

        st.divider()
        col_save, col_cancel = st.columns(2)

        if col_save.button("✅ 確認並存至雲端", type="primary"):
            # 套用名字修正到 data
            fixed_data = []
            for row in pending["data"]:
                fixed_row = dict(row)
                fixed_row["name"] = corrected_names.get(row["name"], row["name"])
                fixed_data.append(fixed_row)

            # 更新 name_order（用修正後的名字）
            fixed_order = [corrected_names.get(n, n) for n in original_order]
            # 去重（保留順序）
            seen = []
            for n in fixed_order:
                if n not in seen:
                    seen.append(n)

            final_content = dict(pending)
            final_content["data"] = fixed_data
            final_content["name_order"] = seen

            if save_to_drive(final_content):
                st.session_state.pending_content = None
                st.balloons()

        if col_cancel.button("🗑️ 取消，重新上傳"):
            st.session_state.pending_content = None
            st.rerun()

    else:
        # Step 1：上傳圖片 + AI 辨識
        uploaded_file = st.file_uploader("請上傳班表圖片", type=["png", "jpg", "jpeg"])

        default_year, default_month = datetime.now().year, datetime.now().month
        if uploaded_file:
            match = re.match(r"(\d{4})(\d{2})_", uploaded_file.name)
            if match:
                default_year, default_month = int(match.group(1)), int(match.group(2))
                st.sidebar.info(f"從檔名偵測：{default_year} 年 {default_month} 月")

        col1, col2 = st.columns(2)
        y = col1.number_input("年份", min_value=2024, value=default_year)
        m = col2.number_input("月份", min_value=1, max_value=12, value=default_month)

        if uploaded_file:
            st.image(uploaded_file, caption="上傳的班表", width=300)
            if st.button("🚀 開始 AI 辨識"):
                with st.spinner("AI 正在看圖，約 10~20 秒..."):
                    try:
                        img = Image.open(uploaded_file)
                        model = genai.GenerativeModel('gemini-3.1-pro-preview')
                        prompt = f"""
                        這是一張達揚連鎖藥局的 {y} 年 {m} 月排班表。請幫我精準辨識表格中所有藥師的排班資訊。
                        請回傳一個乾淨的 JSON Array，不要包含 ```json 這樣的 Markdown 標記，直接給中括號包起來的陣列。
                        重要：請依照班表從上到下的人員順序排列，同一個人的所有班次要連續列出。

                        格式範例：
                        [
                            {{"name": "志銓", "day": 1, "loc": "日", "type": "晚"}},
                            {{"name": "若萍", "day": 1, "loc": "達", "type": "早"}}
                        ]

                        欄位規則：
                        - name: 員工姓名
                        - day: 1 到 31 的整數
                        - loc: 僅能填入 "達"、"日" 或 "健"
                        - type: 僅能填入 "早"、"晚" 或 "全"
                        """
                        response = model.generate_content([prompt, img])
                        result_text = response.text.strip()

                        json_match = re.search(r'\[.*\]', result_text, re.DOTALL)
                        if json_match:
                            result_text = json_match.group(0)

                        parsed_data = json.loads(result_text)
                        name_order = extract_name_order(parsed_data)

                        # 暫存，進入 Step 2 確認
                        st.session_state.pending_content = {
                            "year": int(y),
                            "month": int(m),
                            "data": parsed_data,
                            "name_order": name_order,
                            "updated_at": str(datetime.now())
                        }
                        st.rerun()

                    except json.JSONDecodeError:
                        st.error("⚠️ AI 回傳格式非標準 JSON，請重試。")
                        st.text("原始回傳：\n" + result_text)
                    except Exception as e:
                        st.error(f"辨識出錯：{e}")

else:
    # ── 一般使用者介面 ───────────────────────────────────────────────
    st.title("🏥 達揚藥局 個人班表系統")
    stored = load_from_drive()

    if not stored:
        st.warning("⚠️ 雲端無資料，請管理員進行上傳。")
    else:
        st.info(f"📅 目前版本：{stored['year']} 年 {stored['month']} 月班表")

        processed_list = []
        loc_map = {"達": "達揚", "日": "日揚", "健": "健揚"}
        full_df_raw = pd.DataFrame(stored['data'])

        for _, row in full_df_raw.iterrows():
            f_loc = loc_map.get(row['loc'], "未知")
            st_t, en_t = get_shift_times(
                row['name'], f_loc, row['type'],
                stored['year'], stored['month'], row['day']
            )
            processed_list.append({
                "name": row['name'], "day": row['day'],
                "start": st_t, "end": en_t,
                "shift": f"{f_loc}{row['type']}班"
            })
        full_df = pd.DataFrame(processed_list)

        # 下拉選單：用 name_order 維持班表順序並加編號
        # 若舊版 JSON 沒有 name_order，fallback 到 sorted
        stored_order = stored.get("name_order", sorted(full_df['name'].unique().tolist()))
        # 只保留本月實際有班的人（防止 name_order 有遺留名字）
        available_names = set(full_df['name'].unique())
        ordered_names = [n for n in stored_order if n in available_names]
        # 有 name_order 沒涵蓋到的人（理論上不應發生，但保底加進去）
        for n in sorted(available_names - set(ordered_names)):
            ordered_names.append(n)

        numbered_options = [f"{i+1}. {name}" for i, name in enumerate(ordered_names)]

        sel_display = st.selectbox("👤 選擇您的姓名", options=numbered_options)
        # 從 "1. 志銓" 取回 "志銓"
        sel_name = sel_display.split(". ", 1)[1]

        if sel_name:
            my_df = full_df[full_df['name'] == sel_name].sort_values("day")

            with st.expander("🔍 本月共班夥伴 (重疊滿5小時)"):
                for _, my_s in my_df.iterrows():
                    others = full_df[
                        (full_df['day'] == my_s['day']) & (full_df['name'] != sel_name)
                    ]
                    partners = []
                    for _, op in others.iterrows():
                        ov = min(my_s['end'], op['end']) - max(my_s['start'], op['start'])
                        if ov.total_seconds() / 3600 >= 5:
                            partners.append(op['name'])
                    st.write(f"**{my_s['day']}日**：{', '.join(partners) if partners else '獨自值班'}")

            if st.button("🖼️ 產生我的手機班表圖"):
                with st.spinner("產生中..."):
                    img_bytes = generate_calendar_image(my_df, stored['year'], stored['month'], sel_name)
                    st.image(img_bytes, width=375)

st.markdown("---")
st.caption(f"© {datetime.now().year} 達揚連鎖藥局 | 志銓")
