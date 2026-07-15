import os
import json
import time
import datetime
import threading
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ----------------------------------------------------
# 1. 核心數據結構定義
# ----------------------------------------------------
TWL_ORDER = ["CEN", "ADM", "TST", "JOR", "YMT", "MOK", "PRE", "SSP", "CSW", "LCK", "MEF", "LAK", "KWF", "KWH", "TWH", "TSW"]
TKL_ORDER = ["NOP", "QUB", "YAT", "TIK", "TKO", "HAH", "POA"]

TRAVEL_TIME_CONFIG = {}
ACTIVE_TRAINS = {}         # 雲端運行的虛擬列車狀態
LAST_API_STATE = {}        # 用於比對 ttnt 從 0 變大的上一次狀態
PEAK_TRAIN_COUNT = 0       # 當日最高全線用車量
LOCK = threading.Lock()

# 數據歸檔資料夾
DATA_DIR = os.path.join(app.root_path, 'data_archive')
os.makedirs(DATA_DIR, exist_ok=True)

# ----------------------------------------------------
# 2. 自動按月數據歸檔機制
# ----------------------------------------------------
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
# 3. 路線與車站站序輔助
# ----------------------------------------------------
def get_previous_station(next_sta, direction, line, dest=""):
    next_sta, direction, line = next_sta.upper().strip(), direction.upper().strip(), line.upper().strip()
    if line == "TWL":
        if next_sta not in TWL_ORDER: return None
        idx = TWL_ORDER.index(next_sta)
        return TWL_ORDER[idx - 1] if direction == "UP" else (TWL_ORDER[idx + 1] if idx < len(TWL_ORDER) - 1 else None)
    return None

# ----------------------------------------------------
# 4. 24小時核心事件監聽與物理引擎
# ----------------------------------------------------
def update_live_core_engine(api_train_data):
    global PEAK_TRAIN_COUNT
    now = time.time()
    current_active_count = 0
    
    for train in api_train_data:
        line = train.get('line', '').upper()
        sta = train.get('station', '').upper()
        dir = train.get('direction', '').upper()
        ttnt = int(train.get('ttnt', -1))
        dest = train.get('dest', '')
        
        state_key = f"{line}_{sta}_{dir}"
        last_state = LAST_API_STATE.get(state_key)
        
        if ttnt == 0:
            if not last_state or last_state.get('ttnt') > 0:
                archive_log_event(line, sta, dir, "ARRIVED", dest)
                prev_sta = get_previous_station(sta, dir, line, dest)
                if prev_sta:
                    train_id = f"{line}_{dir}_{prev_sta}_{sta}"
                    if train_id in ACTIVE_TRAINS:
                        ACTIVE_TRAINS[train_id]['status'] = 'arrived'
                        ACTIVE_TRAINS[train_id]['ratio'] = 1.0

        elif ttnt > 0 and last_state and last_state.get('ttnt') == 0:
            archive_log_event(line, sta, dir, "DEPARTED", dest)
            next_sta = None
            if line == "TWL":
                idx = TWL_ORDER.index(sta)
                next_sta = TWL_ORDER[idx + 1] if dir == "UP" and idx < len(TWL_ORDER) - 1 else (TWL_ORDER[idx - 1] if dir == "DOWN" and idx > 0 else None)
            
            if next_sta:
                train_id = f"{line}_{dir}_{sta}_{next_sta}"
                time_key = f"{sta}_{next_sta}"
                duration = TRAVEL_TIME_CONFIG.get(time_key, 110)
                
                with LOCK:
                    ACTIVE_TRAINS[train_id] = {
                        "line": line, "direction": dir, "from_sta": sta, "to_sta": next_sta,
                        "dest": dest, "start_time": now, "total_duration_sec": duration,
                        "ratio": 0.0, "status": "cruising"
                    }
                    
        LAST_API_STATE[state_key] = {'ttnt': ttnt, 'timestamp': now}

    with LOCK:
        for tid, t in ACTIVE_TRAINS.items():
            if t['status'] == 'cruising':
                elapsed = now - t['start_time']
                t['ratio'] = min(1.0, elapsed / t['total_duration_sec'])
                if t['ratio'] >= 1.0:
                    t['status'] = 'stopped_at_station'
                current_active_count += 1
                
        if current_active_count > PEAK_TRAIN_COUNT:
            PEAK_TRAIN_COUNT = current_active_count

def mock_or_fetch_api():
    """24小時不間斷輪詢港鐵官方正式開放數據 API，獲取荃灣線實時列車動態"""
    # 挑選荃灣線核心樞紐車站來監聽整條線的上下行列車
    MONITOR_STATIONS = ["CEN", "ADM", "TST", "MOK", "MEF", "LAK", "TSW"]
    BASE_URL = "https://rt.mtr.com.hk/rt_ticket-val/data/v1/transport/mtr/getSchedule.php"
    
    while True:
        formatted_trains = []
        for sta in MONITOR_STATIONS:
            try:
                # 呼叫官方正式 API
                response = requests.get(BASE_URL, params={"line": "TWL", "sta": sta}, timeout=5)
                if response.status_code == 200:
                    res_json = response.json()
                    
                    # 港鐵官方格式解析：data -> LINE-STA -> UP / DOWN
                    if "data" in res_json:
                        key = f"TWL-{sta}"
                        if key in res_json["data"]:
                            sta_data = res_json["data"][key]
                            
                            for direction in ["UP", "DOWN"]:
                                if direction in sta_data:
                                    for t_info in sta_data[direction]:
                                        ttnt = t_info.get("ttnt", -1)
                                        dest = t_info.get("dest", "")
                                        
                                        if ttnt != -1:
                                            formatted_trains.append({
                                                "line": "TWL",
                                                "station": sta,
                                                "direction": direction,
                                                "ttnt": int(ttnt),
                                                "dest": dest
                                            })
            except Exception as e:
                print(f"抓取車站 {sta} 異常: {e}")
            time.sleep(0.5) # 避免請求過快被港鐵防火牆封鎖
            
        # 將這一輪抓到的全線實時數據，送入我們的物理大腦比對
        if formatted_trains:
            try:
                update_live_core_engine(formatted_trains)
            except Exception as e:
                print(f"物理引擎運算異常: {e}")
                
        # 每 15 秒重新全線輪詢一次
        time.sleep(15)

# ----------------------------------------------------
# 5. 後台與地圖路由
# ----------------------------------------------------
@app.route('/')
def admin_page():
    return render_template('admin.html')

@app.route('/map')
def map_page():
    return render_template('map.html')

@app.route('/api/admin/dashboard')
def admin_dashboard_api():
    line_filter = request.args.get('line', 'TWL').upper()
    with LOCK:
        up_count = sum(1 for t in ACTIVE_TRAINS.values() if t['line'] == line_filter and t['direction'] == 'UP' and t['status'] == 'cruising')
        down_count = sum(1 for t in ACTIVE_TRAINS.values() if t['line'] == line_filter and t['direction'] == 'DOWN' and t['status'] == 'cruising')
        
        train_positions = []
        for tid, t in ACTIVE_TRAINS.items():
            if t['line'] == line_filter:
                train_positions.append({
                    "id": tid, "from": t['from_sta'], "to": t['to_sta'],
                    "direction": t['direction'], "ratio": t['ratio'], "status": t['status']
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
