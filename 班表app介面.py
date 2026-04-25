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
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload  # Fix: MediaIoBaseUpload

# ==========================================
# 1. 核心設定與 API 連線 (Secrets)
# ==========================================
st.set_page_config(page_title="達揚連鎖藥局班表系統", layout="centered", page_icon="🏥")

FOLDER_ID = "1UiYKHzW6tJq6gjML56nktUh5IugLqeaS"
FONT_FILE = "NotoSansTC-Regular.ttf"
JSON_FILE_NAME = "schedule_data.json"

# Gemini API
try:
    api_key = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=api_key)
    st.sidebar.success("✅ Gemini AI 連線成功")
except Exception:
    st.sidebar.warning("⚠️ 未偵測到 Gemini 金鑰")
    api_key = st.sidebar.text_input("🔑 手動輸入 API Key", type="password")
    if api_key:
        genai.configure(api_key=api_key)

# GDrive secret 存在性快速檢查（不發 network request，只看 secrets）
if "gcp_service_account" in st.secrets:
    st.sidebar.success("✅ Google Drive 金鑰已設定")
else:
    st.sidebar.error("❌ Google Drive 金鑰未設定（上傳功能將無法存檔）")

# ==========================================
# 2. 工具函數: Google Drive 雲端對接
# ==========================================

def get_gdrive_service():
    try:
        # json round-trip 確保 AttrDict 完全轉成 plain dict，同時處理所有 \n 問題
        import json as _json
        creds_dict = _json.loads(_json.dumps(dict(st.secrets["gcp_service_account"])))
        creds = service_account.Credentials.from_service_account_info(creds_dict)
        scoped_creds = creds.with_scopes(['https://www.googleapis.com/auth/drive'])
        return build('drive', 'v3', credentials=scoped_creds)
    except KeyError:
        return None
    except Exception as e:
        st.sidebar.error(f"GCP 連線錯誤: {e}")
        return None


def load_from_drive():
    """從 Google Drive 下載最新 JSON 資料"""
    service = get_gdrive_service()
    if not service:
        return None
    try:
        query = f"name = '{JSON_FILE_NAME}' and '{FOLDER_ID}' in parents and trashed = false"
        results = service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])
        if not files:
            return None
        file_id = files[0]['id']
        request = service.files().get_media(fileId=file_id)
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
    """將 JSON 資料同步回 Google Drive"""
    service = get_gdrive_service()
    if not service:
        st.warning("⚠️ 尚未設定 Google Drive 金鑰！目前僅顯示 AI 辨識結果（未存檔）：")
        st.json(content)
        return
    try:
        json_bytes = json.dumps(content, ensure_ascii=False, indent=4).encode('utf-8')
        # Fix: MediaFileUpload 不接受 BytesIO，改用 MediaIoBaseUpload
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
    except Exception as e:
        st.error(f"存檔至雲端失敗: {e}")


# ==========================================
# 3. 工具函數: 手機班表繪圖
# ==========================================

def generate_calendar_image(user_df, year, month, name):
    """產生手機版月曆圖。

    修正點：
    - 原版固定 img_h=900，6週月份每格只剩 125px，嚴重壓縮
    - 改為固定 CELL_H=135，img_h 隨週數動態計算，長寬比不再失真
    """
    HEADER_H = 150
    CELL_H = 135
    img_w = 750
    cell_w = img_w // 7  # 107px

    cal = calendar.monthcalendar(year, month)
    img_h = HEADER_H + CELL_H * len(cal)  # 動態高度：4週=690, 5週=825, 6週=960

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
# 4. 核心時間邏輯函數
# ==========================================

def get_shift_times(name, loc, shift_type, y, m, d):
    """計算起迄時間（志銓專屬邏輯保留）"""
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
# 5. 主程式介面
# ==========================================

is_admin = st.sidebar.checkbox("🛠️ 管理員模式 (更新班表圖)")

if is_admin:
    st.header("📤 管理員：上傳新班表")
    uploaded_file = st.file_uploader("請上傳班表圖片", type=["png", "jpg", "jpeg"])

    default_year, default_month = datetime.now().year, datetime.now().month
    if uploaded_file:
        match = re.match(r"(\d{4})(\d{2})_", uploaded_file.name)
        if match:
            default_year, default_month = int(match.group(1)), int(match.group(2))
            st.sidebar.info(f"💾 從檔名偵測到年份：{default_year}，月份：{default_month}")

    col1, col2 = st.columns(2)
    y = col1.number_input("年份", min_value=2024, value=default_year)
    m = col2.number_input("月份", min_value=1, max_value=12, value=default_month)

    if uploaded_file:
        st.image(uploaded_file, caption="上傳的班表", width=300)
        if st.button("🚀 開始 AI 辨識並同步至雲端"):
            with st.spinner("AI 正在發功看圖中，大約需要 10~20 秒，請稍候..."):
                try:
                    img = Image.open(uploaded_file)
                    model = genai.GenerativeModel('gemini-3.1-pro-preview')
                    prompt = f"""
                    這是一張達揚連鎖藥局的 {y} 年 {m} 月排班表。請幫我精準辨識表格中所有藥師的排班資訊。
                    請回傳一個乾淨的 JSON Array，不要包含 ```json 這樣的 Markdown 標記，直接給中括號包起來的陣列。

                    格式範例如下：
                    [
                        {{"name": "志銓", "day": 1, "loc": "日", "type": "晚"}},
                        {{"name": "若萍", "day": 1, "loc": "達", "type": "早"}}
                    ]

                    欄位規則嚴格限制：
                    - name: 員工姓名 (例如: 志銓、若萍、修慧、佩蘭、可安等)
                    - day: 1 到 31 的整數
                    - loc: 僅能填入 "達"、"日" 或 "健"
                    - type: 僅能填入 "早"、"晚" 或 "全"
                    """
                    response = model.generate_content([prompt, img])
                    result_text = response.text.strip()

                    # Fix: 直接 regex 抓 [...] 區塊，比 split("\n")[1:-1] 更穩健
                    json_match = re.search(r'\[.*\]', result_text, re.DOTALL)
                    if json_match:
                        result_text = json_match.group(0)

                    parsed_data = json.loads(result_text)
                    content = {
                        "year": y,
                        "month": m,
                        "data": parsed_data,
                        "updated_at": str(datetime.now())
                    }
                    save_to_drive(content)

                except json.JSONDecodeError:
                    st.error("⚠️ AI 回傳的格式不是標準 JSON，請重試一次。")
                    st.text("原始回傳內容：\n" + result_text)
                except Exception as e:
                    st.error(f"辨識出錯了：{e}")
else:
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

        sel_name = st.selectbox("👤 選擇您的姓名", options=sorted(full_df['name'].unique()))

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
