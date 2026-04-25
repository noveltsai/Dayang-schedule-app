"""
admin_upload.py — 管理員本機專用腳本
辨識班表圖 → 整理名單 → 存 Google Drive

執行方式：
  python admin_upload.py 圖片.jpg
  python admin_upload.py 圖片.jpg --year 2026 --month 6
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
import io

import google.generativeai as genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from PIL import Image

# ── 設定 ──────────────────────────────────────────────────────────────
BASE = Path(__file__).parent

# 金鑰路徑（可改成你放到非同步資料夾的路徑）
SERVICE_ACCOUNT_FILE = BASE / "service_account.json"

# Gemini API Key（從 .env 或直接填）
GEMINI_API_KEY_FILE = BASE / ".env"

FOLDER_ID = "1UiYKHzW6tJq6gjML56nktUh5IugLqeaS"
JSON_FILE_NAME = "schedule_data.json"


def load_gemini_key() -> str:
    """依序嘗試：.env 檔 → 環境變數 → 互動輸入"""
    if GEMINI_API_KEY_FILE.exists():
        for line in GEMINI_API_KEY_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    import os
    key = os.environ.get("GEMINI_API_KEY", "")
    if key:
        return key
    return input("請輸入 Gemini API Key：").strip()


def get_drive_service():
    creds_dict = json.loads(SERVICE_ACCOUNT_FILE.read_text(encoding="utf-8"))
    creds = service_account.Credentials.from_service_account_info(creds_dict)
    scoped = creds.with_scopes(["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=scoped)


def ocr_schedule(img_path: Path, year: int, month: int) -> list:
    """呼叫 Gemini 辨識班表圖，回傳 parsed data list"""
    api_key = load_gemini_key()
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-3.1-pro-preview")

    img = Image.open(img_path)
    prompt = f"""
    這是一張達揚連鎖藥局的 {year} 年 {month} 月排班表。請幫我精準辨識表格中所有藥師的排班資訊。
    請回傳一個乾淨的 JSON Array，不要包含 ```json 這樣的 Markdown 標記。
    重要：請依照班表從上到下的人員順序排列，同一個人的所有班次要連續列出。

    格式：
    [{{"name": "志銓", "day": 1, "loc": "日", "type": "晚"}}, ...]

    欄位規則：
    - name: 員工姓名
    - day: 1~31 整數
    - loc: 僅能填 "達"、"日" 或 "健"
    - type: 僅能填 "早"、"晚" 或 "全"
    """

    response = model.generate_content([prompt, img])
    result_text = response.text.strip()

    m = re.search(r"\[.*\]", result_text, re.DOTALL)
    if m:
        result_text = m.group(0)

    return json.loads(result_text)


def extract_name_order(data: list) -> list:
    seen = []
    for row in data:
        name = row.get("name", "").strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def review_names(name_order: list) -> list:
    """讓管理員在 terminal 確認/修正辨識到的名字"""
    print("\n── 辨識到的員工名單（依班表順序）──")
    corrected = []
    for i, name in enumerate(name_order):
        fix = input(f"  {i+1}. {name}（Enter 保留，或輸入修正名字）：").strip()
        corrected.append(fix if fix else name)
    return corrected


def apply_name_corrections(data: list, old_order: list, new_order: list) -> list:
    mapping = dict(zip(old_order, new_order))
    return [{**row, "name": mapping.get(row["name"], row["name"])} for row in data]


def save_to_drive(service, content: dict) -> str:
    """更新或建立 Drive 上的 schedule_data.json，回傳 file_id"""
    json_bytes = json.dumps(content, ensure_ascii=False, indent=4).encode("utf-8")
    media = MediaIoBaseUpload(io.BytesIO(json_bytes), mimetype="application/json", resumable=True)

    query = f"name = '{JSON_FILE_NAME}' and '{FOLDER_ID}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get("files", [])

    if files:
        file_id = files[0]["id"]
        service.files().update(fileId=file_id, media_body=media).execute()
        print(f"✅ 已更新 Drive 上的 {JSON_FILE_NAME}（ID: {file_id}）")
    else:
        meta = {"name": JSON_FILE_NAME, "parents": [FOLDER_ID]}
        result = service.files().create(body=meta, media_body=media, fields="id").execute()
        file_id = result["id"]
        print(f"✅ 已建立 {JSON_FILE_NAME}（ID: {file_id}）")
        print(f"   ⚠️  請到 Drive 把這個檔案設為「任何知道連結的人可以檢視」")
        print(f"   然後把 File ID 更新到 Streamlit Secrets：GDRIVE_FILE_ID = \"{file_id}\"")

    return file_id


def main():
    parser = argparse.ArgumentParser(description="達揚班表 OCR 上傳工具")
    parser.add_argument("image", help="班表圖片路徑")
    parser.add_argument("--year", type=int, default=datetime.now().year)
    parser.add_argument("--month", type=int, default=datetime.now().month)
    parser.add_argument("--no-review", action="store_true", help="跳過名單確認步驟")
    args = parser.parse_args()

    img_path = Path(args.image)
    if not img_path.exists():
        print(f"❌ 找不到圖片：{img_path}")
        sys.exit(1)

    print(f"📷 辨識 {img_path.name}（{args.year} 年 {args.month} 月）...")
    try:
        data = ocr_schedule(img_path, args.year, args.month)
    except Exception as e:
        print(f"❌ AI 辨識失敗：{e}")
        sys.exit(1)

    print(f"✅ 辨識完成，共 {len(data)} 筆記錄")
    name_order = extract_name_order(data)

    if not args.no_review:
        corrected_order = review_names(name_order)
        data = apply_name_corrections(data, name_order, corrected_order)
        name_order = corrected_order

    content = {
        "year": args.year,
        "month": args.month,
        "data": data,
        "name_order": name_order,
        "updated_at": str(datetime.now()),
    }

    print("\n📤 上傳至 Google Drive...")
    try:
        service = get_drive_service()
        save_to_drive(service, content)
    except Exception as e:
        print(f"❌ 上傳失敗：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
