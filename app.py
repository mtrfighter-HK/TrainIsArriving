import os
import json
import time
import datetime
import threading
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ----------------------------------------------------
# 1. 核心數據定義
# ----------------------------------------------------
TWL_ORDER = ["CEN", "ADM", "TST", "JOR", "YMT", "MOK", "PRE", "SSP", "CSW", "LCK", "MEF", "LAK", "KWF", "KWH", "TWH", "TSW"]

ST_NAMES = {
    "CEN":"中環", "ADM":"金鐘", "TST":"尖沙咀", "JOR":"佐敦", "YMT":"油麻地",
    "MOK":"旺角", "PRE":"太子", "SSP":"深水埗", "CSW":"長沙灣", "LCK":"荔枝角",
    "MEF":"美孚", "LAK":"荔景", "KWF":"葵芳", "KWH":"葵興", "TWH":"大窩口", "TSW":"荃灣"
}

DEFAULT_TRAVEL_TIME = 110 

# ----------------------------------------------------
# 2. 物理引擎狀態儲存
# ----------------------------------------------------
ACTIVE_TRAINS = {}         
LAST_API_STATE = {}        
PEAK_TRAIN_COUNT = 0       
LOCK = threading.Lock()

DATA_DIR = os.path.join(app.root_path, 'data_archive')
os.makedirs(DATA_DIR, exist_ok=True)

# ----------------------------------------------------
# 3. 輔助函數
# ----------------------------------------------------
def get_previous_station(current_sta, direction):
    if current_sta not in TWL_ORDER:
        return None
    idx = TWL_ORDER.index(current_sta)
    if direction == "UP": 
        return TWL_ORDER[idx - 1] if idx > 0 else None
    elif direction == "DOWN": 
        return TWL_ORDER[idx + 1] if idx < len(TWL_ORDER) - 1 else None
    return None

def get_next_station(current_sta, direction):
    if current_sta not in TWL_ORDER:
        return None
    idx = TWL_ORDER.index(current_sta)
    if direction == "UP": 
        return TWL_ORDER[idx + 1] if idx < len(TWL_ORDER) - 1 else None
    elif direction == "DOWN": 
        return TWL_ORDER[idx - 1] if idx > 0 else None
    return None

def archive_log_event(line, station, direction, event_type, dest):
    now = datetime.datetime.now()
    year_month = now.strftime("%Y%m")
    filename = f"{line.upper()}_{year_month}.json"
    filepath = os.path.join(DATA_DIR, filename)
    
    event_data = {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S.%f"),
        "station": station,
        "direction": direction,
        "event": event_type, 
        "dest": dest
    }
    
    with LOCK:
        data_list = []
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data_list = json.load(f)
            except Exception:
                data_list = []
                
        data_list.append(event_data)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data_list, f, ensure_ascii=False, indent=4)

# ----------------------------------------------------
# 5. 核心物理引擎演算法
# ----------------------------------------------------
def update_live_core_engine(api_train_data):
    global PEAK_TRAIN_COUNT
    now = time.time()
    
    with LOCK:
        updated_train_ids = set()

        for train in api_train_data:
            line = train.get('line', 'TWL')
            sta = train.get('station')
            direction = train.get('direction')
            ttnt = train.get('ttnt')
            dest = train.get('dest')
            
            state_key = f"{line}_{sta}_{direction}"
            last_state = LAST_API_STATE.get(state_key)
            
            # 確保 ttnt 為整數
            try:
                ttnt_val = int(ttnt)
            except (ValueError, TypeError):
                continue
            
            if ttnt_val == 0:
                if not last_state or last_state.get('ttnt', -1) > 0:
                    archive_log_event(line, sta, direction, "ARRIVED", dest)
                
                prev_sta = get_previous_station(sta, direction)
                if prev_sta:
                    train_id = f"{line}_{direction}_{prev_sta}_{sta}"
                    updated_train_ids.add(train_id)
                    ACTIVE_TRAINS[train_id] = {
                        "line": line, "direction": direction, "from_sta": prev_sta, "to_sta": sta,
                        "dest": dest, "start_time": now, "total_duration_sec": DEFAULT_TRAVEL_TIME,
                        "ratio": 1.0, "status": "stopped_at_station"
                    }
            
            elif ttnt_val > 0:
                next_sta = get_next_station(sta, direction)
                if next_sta:
                    train_id = f"{line}_{direction}_{sta}_{next_sta}"
                    
                    if last_state and last_state.get('ttnt') == 0:
                        archive_log_event(line, sta, direction, "DEPARTED", dest)
                    
                    if train_id not in ACTIVE_TRAINS or ACTIVE_TRAINS[train_id]['status'] != 'cruising':
                        ACTIVE_TRAINS[train_id] = {
                            "line": line, "direction": direction, "from_sta": sta, "to_sta": next_sta,
                            "dest": dest, "start_time": now, "total_duration_sec": DEFAULT_TRAVEL_TIME,
                            "ratio": 0.0, "status": "cruising"
                        }
                    updated_train_ids.add(train_id)
            
            LAST_API_STATE[state_key] = {'ttnt': ttnt_val, 'timestamp': now}

        current_active_count = 0
        for tid, t in list(ACTIVE_TRAINS.items()):
            if t['status'] == 'cruising':
                elapsed = now - t['start_time']
                t['ratio'] = min(1.0, elapsed / t['total_duration_sec'])
                
                if t['ratio'] >= 1.0:
                    t['status'] = 'stopped_at_station'
                
                current_active_count += 1
            elif t['status'] == 'stopped_at_station':
                current_active_count += 1
                
            if tid not in updated_train_ids and (now - t['start_time'] > 180):
                ACTIVE_TRAINS.pop(tid, None)
                
        if current_active_count > PEAK_TRAIN_COUNT:
            PEAK_TRAIN_COUNT = current_active_count

# ----------------------------------------------------
# 6. 港鐵官方 API 輪詢監聽器（保持不變）
# ----------------------------------------------------
def mtr_api_fetcher_thread():
    BASE_URL = "https://rt.mtr.com.hk/rt_ticket-val/data/v1/transport/mtr/getSchedule.php"
    
    print("[MTR Core] 🚀 實時數據監聽背景線程正在強行啟動中...", flush=True)
    while True:
        formatted_trains = []
        success_count = 0
        fail_count = 0
        
        for sta in TWL_ORDER:
            try:
                response = requests.get(BASE_URL, params={"line": "TWL", "sta": sta}, timeout=5)
                if response.status_code == 200:
                    res_json = response.json()
                    success_count += 1
                    if "data" in res_json:
                        key = f"TWL-{sta}"
                        if key in res_json["data"]:
                            sta_data = res_json["data"][key]
                            for direction in ["UP", "DOWN"]:
                                if direction in sta_data:
                                    for t_info in sta_data[direction]:
                                        ttnt = t_info.get("ttnt", -1)
                                        dest = t_info.get("dest", "")
                                        if ttnt != -1 and ttnt != "":
                                            formatted_trains.append({
                                                "line": "TWL",
                                                "station": sta,
                                                "direction": direction,
                                                "ttnt": int(ttnt),
                                                "dest": dest
                                            })
                else:
                    fail_count += 1
            except Exception as e:
                fail_count += 1
            time.sleep(0.15)
            
        print(f"[MTR Log] {datetime.datetime.now().strftime('%H:%M:%S')} | 車站輪詢成功: {success_count}/16 | 失敗: {fail_count} | 捕捉到實時列車班次: {len(formatted_trains)} 班", flush=True)
        
        if formatted_trains:
            try:
                update_live_core_engine(formatted_trains)
                print(f"[MTR Log] 物理引擎更新完畢。當前活動列車總數: {len(ACTIVE_TRAINS)}", flush=True)
            except Exception as e:
                print(f"[MTR Log] ⚠️ 物理引擎更新異常: {e}", flush=True)
                
        time.sleep(12)


# ----------------------------------------------------
# 🔒 關鍵修正：確保執行緒安全啟動，加入 flush=True 逼迫 Render 立刻印出 Log
# ----------------------------------------------------
THREAD_STARTED = False

@app.before_request
def start_background_threads():
    global THREAD_STARTED
    if not THREAD_STARTED:
        with LOCK:
            if not THREAD_STARTED:
                print("[MTR Core] 🛑 檢測到系統首次請求，正在建立背景監聽線程...", flush=True)
                t = threading.Thread(target=mtr_api_fetcher_thread, daemon=True)
                t.start()
                THREAD_STARTED = True
                print("[MTR Core] 🎉 背景監聽線程已成功成功派駐！", flush=True)


# ----------------------------------------------------
# 7. 後端路由（保持不變）
# ----------------------------------------------------
@app.route('/')
def map_page():
    return render_template('map.html')

@app.route('/admin')
def admin_page():
    return render_template('admin.html')

@app.route('/api/station/schedule')
def station_schedule_api():
    sta = request.args.get('sta', '').upper()
    line = request.args.get('line', 'TWL').upper()
    if not sta:
        return jsonify({"error": "Missing station code"}), 400
    BASE_URL = "https://rt.mtr.com.hk/rt_ticket-val/data/v1/transport/mtr/getSchedule.php"
    try:
        response = requests.get(BASE_URL, params={"line": line, "sta": sta}, timeout=5)
        if response.status_code == 200:
            return jsonify(response.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Failed to fetch data"}), 500

@app.route('/api/admin/dashboard')
def admin_dashboard_api():
    line_filter = request.args.get('line', 'TWL').upper()
    with LOCK:
        up_count = sum(1 for t in ACTIVE_TRAINS.values() if t['line'] == line_filter and t['direction'] == 'UP')
        down_count = sum(1 for t in ACTIVE_TRAINS.values() if t['line'] == line_filter and t['direction'] == 'DOWN')
        
        train_positions = []
        for tid, t in ACTIVE_TRAINS.items():
            if t['line'] == line_filter:
                train_positions.append({
                    "id": tid, "from": t['from_sta'], "to": t['to_sta'],
                    "direction": t['direction'], "ratio": t['ratio'], "status": t['status'],
                    "dest": t['dest']
                })
                
    return jsonify({
        "line": line_filter,
        "up_running": up_count,
        "down_running": down_count,
        "peak_use_today": PEAK_TRAIN_COUNT,
        "active_trains": train_positions
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
