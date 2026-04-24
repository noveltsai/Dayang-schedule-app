import streamlit as st
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import calendar
import io
import os
import json
import google.generativeai as genai
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google.oauth2 import service_account

# --- 1. 初始化 Google Drive API ---
def init_drive():
    try:
        if "textkey" in st.secrets:
            info = json.loads(st.secrets["textkey"])
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=['https://www.googleapis.com/auth/drive']
            )
            return build('drive', 'v3', credentials=creds)
        else:
            if os.path.exists("service_account.json"):
                creds = service_account.Credentials.from_service_account_file(
                    "service_account.json", scopes=['https://www.googleapis.com/auth/drive']
                )
                return build('drive', 'v3', credentials=creds)
            return None
    except Exception as e:
        st.sidebar.error(f"雲端連線失敗: {e}")
        return None

drive_service = init_drive()
FILE_NAME = "dayang_schedule_data.json"

# --- 2. 雲端硬碟存取工具 ---
def save_to_drive(data):
    if not drive_service:
        st.error("未偵測到雲端憑證，無法儲存。")
        return
    try:
        query = f"name = '{FILE_NAME}' and trashed = false"
        results = drive_service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])
        
        json_content = json.dumps(data, ensure_ascii=False, indent=2)
        fh = io.BytesIO(json_content.encode('utf-8'))
        media = MediaIoBaseUpload(fh, mimetype='application/json')

        if not files:
            file_metadata = {'name': FILE_NAME}
            drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        else:
            file_id = files[0]['id']
            drive_service.files().update(fileId=file_id, media_body=media).execute()
        st.success("✅ 數據已成功同步至您的 Google Drive！")
    except Exception as e:
        st.error(f"雲端寫入失敗: {e}")

def load_from_drive():
    if not drive_service: return None
    try:
        query = f"name = '{FILE_NAME}' and trashed = false"
        results = drive_service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])
        if not files: return None
        
        file_id = files[0]['id']
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return json.loads(fh.getvalue().decode('utf-8'))
    except:
        return None

# --- 3. 繪圖與邏輯處理 ---
def get_font(size):
    font_paths = [
        "C:\\Windows\\Fonts\\kaiu.ttf", 
        "msjh.ttc",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"
    ]
    for path in font_paths:
        if os.path.exists(path):
            try: return ImageFont.truetype(path, size)
            except: continue
    return ImageFont.load_default()

def get_shift_times(name, location, shift_type, year, month, day):
    start_h, end_h = 9, 17
    is_ri_yang = (location == "日揚")
    if shift_type == "早":
        start_h = 8 if is_ri_yang else 9
        end_h = 17
    elif shift_type == "晚":
        if name == "志銓" and is_ri_yang: start_h, end_h = 17, 22
        else: start_h, end_h = 14, 22
    elif shift_type == "全":
        start_h = 8 if is_ri_yang else 9
        end_h = 22
    return pd.to_datetime(f"{year}-{month:02d}-{day:02d} {start_h:02d}:00"), \
           pd.to_datetime(f"{year}-{month:02d}-{day:02d} {end_h:02d}:00")

def calculate_coworkers(df):
    if df.empty: return df
    df['coworkers'] = ""
    df['display_name'] = df['name'].str.replace("阿", "", regex=False).str.replace("藥師", "", regex=False)
    for date, group in df.groupby('date'):
        indices = group.index.tolist()
        for i in range(len(indices)):
            idx1 = indices[i]
            names = []
            for j in range(len(indices)):
                if i == j: continue
                idx2 = indices[j]
                overlap = (min(df.at[idx1, 'end_dt'], df.at[idx2, 'end_dt']) - 
                           max(df.at[idx1, 'start_dt'], df.at[idx2, 'start_dt'])).total_seconds()/3600
                if overlap >= 5:
                    names.append(df.at[idx2, 'display_name'])
            df.at[idx1, 'coworkers'] = " ".join(names)
    return df

def generate_calendar_image(personal_df, year, month, user_name):
    w, h = 1080, 2400 
    img = Image.new('RGB', (w, h), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    title_f, date_f, shift_f = get_font(80), get_font(40), get_font(55)
    draw.text((80, 120), f"{user_name} - {year}年{month}月 班表", fill=(0,0,0), font=title_f)
    cal = calendar.Calendar(firstweekday=6)
    month_days = cal.monthdayscalendar(year, month)
    margin, header_h = 40, 300
    cell_w, cell_h = (w - margin*2) // 7, (h - header_h - margin) // 6
    colors = {"達揚": (215, 235, 255), "日揚": (255, 252, 220), "健揚": (220, 255, 235), "休息": (248, 248, 248)}
    for r, week in enumerate(month_days):
        for c, day in enumerate(week):
            if day == 0: continue
            x, y = margin + c * cell_w, header_h + r * cell_h
            target_date = f"{year}-{month:02d}-{day:02d}"
            day_shift = personal_df[personal_df['date'] == target_date]
            bg, loc_t, shift_t = colors["休息"], "休", ""
            if not day_shift.empty:
                loc = day_shift.iloc[0]['location']
                bg, loc_t = colors.get(loc, bg), loc
                shift_t = day_shift.iloc[0]['shift'].replace(loc, "")
            draw.rectangle([x+5, y+5, x+cell_w-5, y+cell_h-5], fill=bg, outline=(220, 220, 220), width=2)
            draw.text((x+20, y+20), str(day), fill=(130, 130, 130), font=date_f)
            if loc_t == "休":
                draw.text((x + cell_w//2 - 25, y + cell_h//2 - 25), "休", fill=(190, 190, 190), font=shift_f)
            else:
                draw.text((x + 18, y + cell_h//3), loc_t, fill=(0,0,0), font=shift_f)
                draw.text((x + 28, y + cell_h//3 + 70), shift_t, fill=(0,0,0), font=shift_f)
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    return img_byte_arr.getvalue()

# --- 4. 管理員 AI 辨識 (採用更強模型與優化指令) ---
def admin_ai_parse(image, year, month, api_key):
    try:
        genai.configure(api_key=api_key)
        # 嘗試使用旗艦級 2.5 Pro，它的視覺辨識穩定度比 3.1 更好
        model = genai.GenerativeModel('gemini-2.5-pro') 
        
        prompt = f"""
        你現在是資深藥局管理員。請閱讀這張 {year} 年 {month} 月的班表圖片。
        這是一個複雜的格線表格，請遵循以下思考步驟：
        1. 掃描表格左側：辨識所有人員的「姓名」與對應的「人員編碼」。
        2. 橫向掃描：針對每個人員，找出其對應日期（1號到月底）的班次。
        3. 班次縮寫轉換：'達'=達揚, '日'=日揚, '健'=健揚。
        4. 班次類型：'早'=早班, '晚'=晚班, '全'=全班。
        5. 特別注意：如果格子裡有手寫文字或複雜符號，請盡力辨識地點與班別。

        請輸出完整的 JSON Array 格式，欄位包括：
        "name": 姓名, "code": 人員編碼, "day": 日期(數字), "loc": 地點(達/日/健), "type": 類型(早/晚/全)。
        
        嚴格禁止輸出任何 Markdown 標籤或解釋，只輸出 JSON 內容。
        """
        
        response = model.generate_content([prompt, image])
        text = response.text.strip()
        
        # 強化 JSON 提取邏輯
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
            
        return json.loads(text.strip())
    except Exception as e:
        st.error(f"AI 辨識錯誤: {e}")
        # 備援模型建議
        st.info("提示：如果持續失敗，請確認圖片解析度，或嘗試在後台切換模型名稱。")
        return None

# --- 5. Streamlit 介面渲染 ---
st.set_page_config(page_title="達揚班表查詢系統", layout="wide", page_icon="🏥")
mode = st.sidebar.radio("🚀 系統模式", ["👤 使用者查詢", "🔑 管理員更新"])

if mode == "🔑 管理員更新":
    st.title("🛡️ 班表管理員後台")
    # 這裡可以動態選擇模型，讓主人自己試驗最強的
    selected_model = st.sidebar.selectbox("選擇辨識模型", ["gemini-2.5-pro", "gemini-3.1-pro-preview", "gemini-3-pro-image-preview"])
    
    admin_key = st.sidebar.text_input("Gemini API Key", type="password")
    y = st.number_input("設定年份", 2024, 2030, datetime.now().year)
    m = st.selectbox("設定月份", range(1, 13), index=datetime.now().month-1)
    up_img = st.file_uploader("📤 上傳班表圖片", type=["jpg", "png", "jpeg"])
    
    if up_img and st.button("🚀 開始解析並同步至雲端"):
        if not admin_key: st.warning("請先輸入 API Key"); st.stop()
        with st.spinner(f"正在使用 {selected_model} 進行深度解析..."):
            parsed = admin_ai_parse(Image.open(up_img), y, m, admin_key)
            if parsed:
                storage_obj = {"year": y, "month": m, "data": parsed, "updated_at": str(datetime.now())}
                save_to_drive(storage_obj)

else:
    st.title("🏥 達揚連鎖藥局 AI 辨識 個人班表系統")
    stored = load_from_drive()
    if not stored:
        st.warning("⚠️ 目前雲端硬碟中尚無班表數據，請管理員進行更新。")
    else:
        st.info(f"📅 目前提供：**{stored['year']} 年 {stored['month']} 月** 班表")
        df_raw = pd.DataFrame(stored['data'])
        processed = []
        for _, item in df_raw.iterrows():
            loc_full = {"達": "達揚", "日": "日揚", "健": "健揚"}.get(item['loc'], "未知")
            start, end = get_shift_times(item['name'], loc_full, item['type'], stored['year'], stored['month'], item['day'])
            processed.append({
                "name": item['name'], 
                "code": item.get('code', ""), 
                "date": f"{stored['year']}-{stored['month']:02d}-{item['day']:02d}", 
                "location": loc_full, 
                "shift": f"{loc_full}{item['type']}班", 
                "start_dt": start, 
                "end_dt": end
            })
        
        full_df = calculate_coworkers(pd.DataFrame(processed))
        names = sorted(full_df['name'].unique().tolist())
        sel_name = st.selectbox("👤 請選擇您的姓名", options=names)
        
        if st.button("🖼️ 立即產生個人班表"):
            p_df = full_df[full_df['name'] == sel_name]
            st.write(f"你好，{sel_name}！(編碼: {p_df['code'].iloc[0]}) 這是您的專屬班表：")
            c1, c2 = st.columns([1, 1])
            with c1:
                img_data = generate_calendar_image(p_df, stored['year'], stored['month'], sel_name)
                st.image(img_data, width=400)
                st.download_button("📥 下載月曆圖", img_data, f"{sel_name}_月曆.png")
            with c2:
                st.dataframe(p_df[['date', 'shift', 'coworkers', 'code']], use_container_width=True)
                csv = p_df[['date', 'shift', 'coworkers']].to_csv(index=False).encode('utf-8-sig')
                st.download_button("📥 下載 Google 日曆 CSV", csv, f"{sel_name}.csv")

st.markdown("---")
st.caption("© 2026 達揚連鎖藥局 | Powered by 志銓with Gemini")