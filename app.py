import time
import threading
import requests
from flask import Flask, jsonify, request, render_template

app = Flask(__name__, static_folder='.', static_url_path='')

# 全局數據存儲
ACTIVE_TRAINS = {}
PEAK_TRAINS_TODAY = {"TWL": 8, "TKL": 6}
LOCK = threading.Lock()

TWL_ORDER = ["CEN", "ADM", "TST", "JOR", "YMT", "MOK", "PRE", "SSP", "CSW", "LCK", "MEF", "LAK", "KWF", "KWH", "TWH", "TSW"]

STATION_COORDS = {
    "CEN": [22.28185, 114.1581], "ADM": [22.27945, 114.1641], "TST": [22.2989, 114.1719],
    "JOR": [22.3049, 114.1717],  "YMT": [22.3129, 114.1699],  "MOK": [22.3193, 114.1694],
    "PRE": [22.3256, 114.1687],  "SSP": [22.3307, 114.1623],  "CSW": [22.3350, 114.1575],
    "LCK": [22.3368, 114.1492],  "MEF": [22.3375, 114.1385],  "LAK": [22.3486, 114.1274],
    "KWF": [22.3568, 114.1317],  "KWH": [22.3646, 114.1313],  "TWH": [22.3707, 114.1281],
    "TSW": [22.3732, 114.1178]
}

TRAVEL_TIME_CONFIG = {
    "CEN_ADM": 120, "ADM_TST": 180, "TST_JOR": 80,  "JOR_YMT": 80,
    "YMT_MOK": 80,  "MOK_PRE": 70,  "PRE_SSP": 90,  "SSP_CSW": 80,
    "CSW_LCK": 80,  "LCK_MEF": 90,  "MEF_LAK": 110, "LAK_KWF": 100,
    "KWF_KWH": 90,  "KWH_TWH": 90,  "TWH_TSW": 120
}

def interpolate_coords(from_code, to_code, ratio):
    p1 = STATION_COORDS.get(from_code)
    p2 = STATION_COORDS.get(to_code)
    if not p1 or not p2:
        return 22.321, 114.170
    r = max(0.0, min(1.0, ratio))
    lat = p1[0] + (p2[0] - p1[0]) * r
    lng = p1[1] + (p2[1] - p1[1]) * r
    return lat, lng

def fetch_mtr_data():
    global ACTIVE_TRAINS
    while True:
        try:
            url = "https://rt.mtr.com.hk/tickets/bycategory.html?category=TRN&line=TWL"
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get("status") == 1:
                    update_train_physics(data.get("results", []))
        except Exception as e:
            print(f"[API 錯誤] 無法獲取港鐵數據: {e}")
        time.sleep(12)

def update_train_physics(raw_trains):
    global ACTIVE_TRAINS
    with LOCK:
        current_time = time.time()
        for train in raw_trains:
            line = train.get("line", "TWL").upper()
            if line != "TWL": continue
            station = train.get("station", "").upper()
            direction = train.get("direction", "").upper()
            dest = train.get("dest", "").upper()
            ttnt = int(train.get("ttnt", 99))

            idx = TWL_ORDER.index(station) if station in TWL_ORDER else -1
            if idx == -1: continue

            if direction == "UP":
                if idx == 0: continue
                from_sta = TWL_ORDER[idx - 1]
                to_sta = station
            else:
                if idx == len(TWL_ORDER) - 1: continue
                from_sta = TWL_ORDER[idx + 1]
                to_sta = station

            train_id = f"{line}_{direction}_{from_sta}_{to_sta}"
            time_key = f"{from_sta}_{to_sta}" if direction == "UP" else f"{to_sta}_{from_sta}"
            total_duration = TRAVEL_TIME_CONFIG.get(time_key, 110)

            if ttnt == 0:
                ratio = 1.0
                status = "stopped_at_station"
            elif ttnt == 1:
                ratio = max(0.5, (total_duration - 45) / total_duration)
                status = "cruising"
            else:
                ratio = 0.1
                status = "cruising"

            lat, lng = interpolate_coords(from_sta, to_sta, ratio)

            # 同時保留舊版地圖所需的欄位名，以及新版後台所需的欄位名
            ACTIVE_TRAINS[train_id] = {
                "id": train_id,
                "line": line,
                "line_code": line,          # 舊版地圖兼容
                "direction": direction,
                "from_sta": from_sta,
                "to_sta": to_sta,
                "from": from_sta,            # 新版後台相容
                "to": to_sta,                # 新版後台相容
                "dest": dest,
                "ratio": ratio,
                "lat": lat,
                "lng": lng,
                "status": status,
                "ttnt": ttnt,                # 舊版邏輯相容
                "last_update": current_time
            }

        # 清除過期列車
        expired_ids = [tid for tid, t in ACTIVE_TRAINS.items() if current_time - t["last_update"] > 120]
        for tid in expired_ids:
            ACTIVE_TRAINS.pop(tid, None)

def smooth_movement_loop():
    global ACTIVE_TRAINS
    while True:
        with LOCK:
            for train_id, train in list(ACTIVE_TRAINS.items()):
                if train["status"] == "cruising" and train["ratio"] < 1.0:
                    time_key = f"{train['from_sta']}_{train['to_sta']}" if train["direction"] == "UP" else f"{train['to_sta']}_{train['from_sta']}"
                    total_duration = TRAVEL_TIME_CONFIG.get(time_key, 110)
                    new_ratio = min(1.0, train["ratio"] + (1.0 / total_duration))
                    train["ratio"] = new_ratio
                    train["lat"], train["lng"] = interpolate_coords(train["from_sta"], train["to_sta"], new_ratio)
                    if new_ratio >= 1.0:
                        train["status"] = "stopped_at_station"
        time.sleep(1)

@app.route('/')
def index():
    return render_template('map.html')

@app.route('/admin')
def admin_page():
    return render_template('admin.html')

@app.route('/api/train-positions')
@app.route('/api/live') # 🌟 雙路由支持！同時兼容舊地圖的 /api/live 請求
def get_train_positions():
    line = request.args.get('line', 'TWL').upper()
    line_trains = [t for t in ACTIVE_TRAINS.values() if t["line"] == line]
    return jsonify({
        "status": "success",
        "trains": line_trains,
        "active_trains": line_trains
    })

@app.route('/api/admin/dashboard')
def admin_dashboard():
    line = request.args.get('line', 'TWL').upper()
    up_count = sum(1 for t in ACTIVE_TRAINS.values() if t["line"] == line and t["direction"] == "UP")
    down_count = sum(1 for t in ACTIVE_TRAINS.values() if t["line"] == line and t["direction"] == "DOWN")
    line_trains = [t for t in ACTIVE_TRAINS.values() if t["line"] == line]
    
    if len(line_trains) > PEAK_TRAINS_TODAY.get(line, 0):
        PEAK_TRAINS_TODAY[line] = len(line_trains)

    return jsonify({
        "up_running": up_count,
        "down_running": down_count,
        "peak_use_today": PEAK_TRAINS_TODAY.get(line, 8),
        "active_trains": line_trains,
        "trains": line_trains
    })

if __name__ == '__main__':
    threading.Thread(target=fetch_mtr_data, daemon=True).start()
    threading.Thread(target=smooth_movement_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, debug=True)
