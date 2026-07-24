import os
import sqlite3
import requests
import threading
import time
import json
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="MTR 實時地圖 - 自動初始化修復版")

# 掛載 static 資料夾
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")

# ==========================================
# 💾 確保資料庫目錄存在並設定路徑
# ==========================================
DB_DIR = "/app/data" if os.path.exists("/app/data") else "."
if DB_DIR != ".":
    os.makedirs(DB_DIR, exist_ok=True)  # 🟢 確保資料夾一定存在

DB_PATH = os.path.join(DB_DIR, "mtr_data.db")

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# 初始化資料庫結構
def init_db():
    conn = get_db()
    conn.execute('''CREATE TABLE IF NOT EXISTS mtr_ttnt (
        id INTEGER PRIMARY KEY,
        timestamp TEXT,
        line TEXT,
        station TEXT,
        direction TEXT,
        dest TEXT,
        ttnt INTEGER,
        is_delay TEXT,
        collected_at TEXT
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS departure_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_time TEXT,
        station TEXT,
        direction TEXT,
        dest TEXT
    )''')
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 📡 背景數據收集器
# ==========================================
def background_collector():
    stations = [
        ("TWL", "CEN"), ("TWL", "ADM"), ("TWL", "TST"), ("TWL", "JOR"),
        ("TWL", "YMT"), ("TWL", "MOK"), ("TWL", "PRE"), ("TWL", "SSP"),
        ("TWL", "CSW"), ("TWL", "LCK"), ("TWL", "MEF"), ("TWL", "LAK"),
        ("TWL", "KWF"), ("TWL", "KWH"), ("TWL", "TWH"), ("TWL", "TSW")
    ]
    
    last_api_state = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    print("🚀 [系統訊息] 背景數據收集服務已啟動！")

    while True:
        try:
            conn = get_db()
            c = conn.cursor()
            now = datetime.now().isoformat()
            inserted_records = 0
            
            for line, sta in stations:
                try:
                    url = f"https://rt.data.gov.hk/v1/transport/mtr/getSchedule.php?line={line}&sta={sta}"
                    r = requests.get(url, headers=headers, timeout=8)
                    
                    if r.status_code == 200:
                        res_json = r.json()
                        if res_json.get('status') == 1:
                            data = res_json.get('data', {}).get(f'{line}-{sta}', {})
                            for direction in ['UP', 'DOWN']:
                                if direction in data:
                                    for train in data[direction]:
                                        ttnt_val = train.get('ttnt')
                                        if ttnt_val is not None and str(ttnt_val).isdigit():
                                            ttnt_int = int(ttnt_val)
                                            dest = train.get('dest')
                                            is_delay_val = train.get('isdelay', 'N')
                                            
                                            c.execute('''INSERT INTO mtr_ttnt 
                                                (timestamp, line, station, direction, dest, ttnt, is_delay, collected_at)
                                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                                                (now, line, sta, direction, dest, ttnt_int, is_delay_val, now))
                                            
                                            inserted_records += 1
                                            
                                            key = f"{sta}_{direction}"
                                            if key in last_api_state:
                                                last_ttnt = last_api_state[key]
                                                if last_ttnt == 0 and ttnt_int > 0:
                                                    c.execute('''INSERT INTO departure_events 
                                                        (event_time, station, direction, dest)
                                                        VALUES (?, ?, ?, ?)''',
                                                        (now, sta, direction, dest))
                                            last_api_state[key] = ttnt_int
                except Exception as sta_err:
                    print(f"⚠️ 車站 {sta} 擷取失敗: {sta_err}")
            
            conn.commit()
            conn.close()
            print(f"✅ [{now}] 成功寫入 {inserted_records} 筆列車紀錄")
        except Exception as e:
            print(f"❌ 數據庫寫入異常: {e}")

        time.sleep(20) # 每 20 秒抓取一輪數據

# 🟢 使用 FastAPI Startup 事件觸發背景線程
@app.on_event("startup")
def start_background_tasks():
    thread = threading.Thread(target=background_collector, daemon=True)
    thread.start()

# ==========================================
# 📬 路由設定
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="map.html", context={})

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse(request=request, name="admin.html", context={})

@app.get("/api/live")
async def get_live_trains():
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT t.line, t.station, t.direction, t.dest, t.ttnt, t.is_delay, t.timestamp
        FROM mtr_ttnt t
        INNER JOIN (
            SELECT station, direction, MAX(id) as max_id
            FROM mtr_ttnt
            WHERE line = 'TWL'
            GROUP BY station, direction
        ) tm ON t.id = tm.max_id
    ''')
    rows = c.fetchall()
    conn.close()
    
    trains = []
    for row in rows:
        trains.append({
            "line": row["line"],
            "station": row["station"],
            "direction": row["direction"],
            "dest": row["dest"],
            "ttnt": row["ttnt"],
            "is_delay": row["is_delay"],
            "timestamp": row["timestamp"]
        })
    return {"status": "success", "data": trains}

@app.get("/api/debug")
async def debug_info():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM mtr_ttnt")
    count = c.fetchone()[0]
    c.execute("SELECT timestamp FROM mtr_ttnt ORDER BY id DESC LIMIT 1")
    last_row = c.fetchone()
    last_ts = last_row[0] if last_row else "無資料"
    conn.close()
    return {"total_records": count, "latest_timestamp": last_ts}

@app.get('/api/admin/departures')
async def api_admin_departures(station: str = "ALL", period: str = "ALL", hour: str = "ALL"):
    conn = get_db()
    cursor = conn.cursor()
    query = "SELECT event_time, station, direction, dest FROM departure_events WHERE 1=1"
    params = []
    
    if station and station != "ALL":
        query += " AND station = ?"
        params.append(station.upper())
        
    if period == "WEEKDAY":
        query += " AND strftime('%w', event_time) BETWEEN '1' AND '5'"
    elif period == "WEEKEND":
        query += " AND (strftime('%w', event_time) = '0' OR strftime('%w', event_time) = '6')"
        
    if hour and hour != "ALL":
        query += " AND strftime('%H', event_time) = ?"
        params.append(f"{int(hour):02d}")
        
    query += " ORDER BY event_time DESC LIMIT 100"
    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()
    conn.close()
    return {"status": "success", "data": [dict(row) for row in rows]}

@app.get('/api/admin/stats')
async def api_admin_stats():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM mtr_ttnt")
        total_records = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM departure_events")
        total_events = cursor.fetchone()[0]
        conn.close()
        return {"total_records": total_records, "total_departures": total_events}
    except Exception:
        return {"total_records": 0, "total_departures": 0}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
