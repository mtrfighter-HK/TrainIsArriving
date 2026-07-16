import os
import json
import time
import math
import datetime
import requests
import threading
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ----------------------------------------------------
# 1. 基礎數據與配置定義
# ----------------------------------------------------
TWL_ORDER = ["CEN", "ADM", "TST", "JOR", "YMT", "MOK", "PRE", "SSP", "CSW", "LCK", "MEF", "LAK", "KWF", "KWH", "TWH", "TSW"]

ST_NAMES = {
    "CEN":"中環", "ADM":"金鐘", "TST":"尖沙咀", "JOR":"佐敦", "YMT":"油麻地",
    "MOK":"旺角", "PRE":"太子", "SSP":"深水埗", "CSW":"長沙灣", "LCK":"荔枝角",
    "MEF":"美孚", "LAK":"荔景", "KWF":"葵芳", "KWH":"葵興", "TWH":"大窩口", "TSW":"荃灣"
}

# 全局共享數據結構
TRACK_FEATURES = []
TRAVEL_TIME_CONFIG = {}
ACTIVE_TRAINS = {}  
LAST_API_STATE = {}
PEAK_TRAIN_COUNT = 0       
LOCK = threading.Lock()

# 數據存檔目錄
DATA_DIR = os.path.join(app.root_path, 'data_archive')
os.makedirs(DATA_DIR, exist_ok=True)

# ----------------------------------------------------
# 2. 空間幾何與經緯度內插運算 (保留元祖核心)
# ----------------------------------------------------
def haversine_distance(coord1, coord2):
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    R = 6371000  
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def precompute_track_spatial(coords):
    points = [[c[1], c[0]] for c in coords]  
    distances = [0.0]
    total_dist = 0.0
    for i in range(len(points) - 1):
        d = haversine_distance(points[i], points[i+1])
        total_dist += d
        distances.append(total_dist)
    return {"points": points, "distances": distances, "total_distance": total_dist}

def interpolate_by_ratio(spatial_data, ratio):
    pts = spatial_data["points"]
    dists = spatial_data["distances"]
    total_dist = spatial_data["total_distance"]
    if ratio <= 0.0: return pts[0]
    if ratio >= 1.0: return pts[-1]
    target_dist = total_dist * ratio
    for i in range(len(dists) - 1):
        if dists[i] <= target_dist <= dists[i+1]:
            seg_len = dists[i+1] - dists[i]
            seg_ratio = (target_dist - dists[i]) / seg_len if seg_len > 0 else 0
            lat = pts[i][0] + (pts[i+1][0] - pts[i][0]) * seg_ratio
            lng = pts[i][1] + (pts[i+1][1] - pts[i][1]) * seg_ratio
            return [lat, lng]
    return pts[-1]

# ----------------------------------------------------
# 3. 車站與路段輔助判斷
# ----------------------------------------------------
def get_previous_station(current_sta, direction):
    if current_sta not in TWL_ORDER: return None
    idx = TWL_ORDER.index(current_sta)
    if direction == "UP": return TWL_ORDER[idx - 1] if idx > 0 else None
    elif direction == "DOWN": return TWL_ORDER[idx + 1] if idx < len(TWL_ORDER) - 1 else None
    return None

def get_next_station(current_sta, direction):
    if current_sta not in TWL_ORDER: return None
    idx = TWL_ORDER.index(current_sta)
    if direction == "UP": return TWL_ORDER[idx + 1] if idx < len(TWL_ORDER) - 1 else None
    elif direction == "DOWN": return TWL_ORDER[idx - 1] if idx > 0 else None
    return None

# 尋找匹配的軌道空間幾何數據 (極其關鍵：將火車綁定到 GeoJSON 路線上)
def find_spatial_data(from_sta, to_sta):
    # 預設直線降級方案
    fallback = {"points": [[22.32, 114.16], [22.33, 114.17]], "distances": [0, 1000], "total_distance": 1000}
    # 在加載的 TRACK_FEATURES 中尋找符合這兩個車站名稱或線段的特徵
    # 這裡採取一個安全策略：如果軌道數據存在，直接提取其坐標
    for feature in TRACK_FEATURES:
        geom = feature.get("geometry", {})
        if geom.get("type") == "LineString":
            coords = geom.get("coordinates", [])
            if coords:
                return precompute_track_spatial(coords)
    return fallback

def archive_log_event(line, station, direction, event_type, dest):
    now = datetime.datetime.now()
    filepath = os.path.join(DATA_DIR, f"{line.upper()}_{now.strftime('%Y%m')}.json")
    event_data = {"timestamp": now.strftime("%Y-%m-%d %H:%M:%S.%f"), "station": station, "direction": direction, "event": event_type, "dest": dest}
    with LOCK:
        data_list = []
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f: data_list = json.load(f)
            except: pass
        data_list.append(event_data)
        with open(filepath, 'w', encoding='utf-8') as f: json.dump(data_list, f, ensure_ascii=False, indent=4)

# ----------------------------------------------------
# 4. 初始化與數據加載
# ----------------------------------------------------
def load_base_files():
    global TRACK_FEATURES
    print("[MTR Core] 🚀 開始載入基礎軌道地圖檔...", flush=True)
    twl_path = os.path.join(app.root_path, 'static', 'Track.TsuenWanLine.geojson')
    if os.path.exists(twl_path):
        with open(twl_path, 'r', encoding='utf-8') as f:
            twl_geo = json.load(f)
            TRACK_FEATURES.extend(twl_geo.get("features", []))
    print(f"[MTR Core] ✅ 軌道加載成功，段數: {len(TRACK_FEATURES)}", flush=True)

# ----------------------------------------------------
# 5. 港鐵數據對接與實時位置步進引擎
# ----------------------------------------------------
def update_live_core_engine(api_train_data):
    global PEAK_TRAIN_COUNT
    now = time.time()
    
    with LOCK:
        updated_train_ids = set()

        for train in api_train_data:
            line = train['line']
            sta = train['station']
            direction = train['direction']
            ttnt_val = train['ttnt']
            dest = train['dest']
            
            state_key = f"{line}_{sta}_{direction}"
            last_state = LAST_API_STATE.get(state_key)
            
            if ttnt_val == 0:
                if not last_state or last_state.get('ttnt', -1) > 0:
                    archive_log_event(line, sta, direction, "ARRIVED", dest)
                
                prev_sta = get_previous_station(sta, direction)
                if prev_sta:
                    train_id = f"{line}_{direction}_{prev_sta}_{sta}"
                    updated_train_ids.add(train_id)
                    if train_id not in ACTIVE_TRAINS:
                        ACTIVE_TRAINS[train_id] = {
                            "line": line, "direction": direction, "from_sta": prev_sta, "to_sta": sta,
                            "dest": dest, "start_time": now, "total_duration_sec": 110,
                            "spatial_data": find_spatial_data(prev_sta, sta), "current_latlng": [22.3, 114.1],
                            "ratio": 1.0, "status": "stopped_at_station"
                        }
            
            elif ttnt_val > 0:
                next_sta = get_next_station(sta, direction)
                if next_sta:
                    train_id = f"{line}_{direction}_{sta}_{next_sta}"
                    updated_train_ids.add(train_id)
                    
                    if last_state and last_state.get('ttnt') == 0:
                        archive_log_event(line, sta, direction, "DEPARTED", dest)
                    
                    if train_id not in ACTIVE_TRAINS:
                        ACTIVE_TRAINS[train_id] = {
                            "line": line, "direction": direction, "from_sta": sta, "to_sta": next_sta,
                            "dest": dest, "start_time": now, "total_duration_sec": 110,
                            "spatial_data": find_spatial_data(sta, next_sta), "current_latlng": [22.3, 114.1],
                            "ratio": 0.0, "status": "cruising"
                        }

            LAST_API_STATE[state_key] = {'ttnt': ttnt_val, 'timestamp': now}

        # 每秒步進更新經緯度位置
        current_active_count = 0
        for tid, t in list(ACTIVE_TRAINS.items()):
            elapsed = now - t['start_time']
            t['ratio'] = min(1.0, elapsed / t['total_duration_sec'])
            
            # 利用元祖演算法更新精準經緯度
            t['current_latlng'] = interpolate_by_ratio(t['spatial_data'], t['ratio'])
            
            if t['ratio'] >= 1.0:
                t['status'] = 'stopped_at_station'
            current_active_count += 1
                
            if tid not in updated_train_ids and (now - t['start_time'] > 180):
                ACTIVE_TRAINS.pop(tid, None)
                
        if current_active_count > PEAK_TRAIN_COUNT:
            PEAK_TRAIN_COUNT = current_active_count

def mtr_api_fetcher_thread():
    BASE_URL = "https://rt.data.gov.hk/v1/transport/mtr/getSchedule.php"
    HEADERS = {"User-Agent": "Mozilla/5.0 (Windows)", "Accept": "application/json"}
    
    load_base_files()
    
    while True:
        formatted_trains = []
        success_count = 0
        fail_count = 0
        
        for sta in TWL_ORDER:
            try:
                response = requests.get(BASE_URL, params={"line": "TWL", "sta": sta}, headers=HEADERS, timeout=5)
                if response.status_code == 200:
                    res_json = response.json()
                    if res_json.get("status") == 1 and "data" in res_json:
                        success_count += 1
                        key = f"TWL-{sta}"
                        if key in res_json["data"]:
                            sta_data = res_json["data"][key]
                            for direction in ["UP", "DOWN"]:
                                if direction in sta_data:
                                    for t_info in sta_data[direction]:
                                        ttnt = t_info.get("ttnt", -1)
                                        dest = t_info.get("dest", "")
                                        if ttnt != -1 and ttnt != "":
                                            ttnt_int = int(ttnt)
                                            if ttnt_int <= 4:
                                                formatted_trains.append({
                                                    "line": "TWL", "station": sta, "direction": direction,
                                                    "ttnt": ttnt_int, "dest": dest
                                                })
                else: fail_count += 1
            except: fail_count += 1
            time.sleep(0.15)
            
        print(f"[MTR Log] {datetime.datetime.now().strftime('%H:%M:%S')} | 輪詢成功: {success_count}/16 | 捕捉 4 分鐘內列車: {len(formatted_trains)} 班", flush=True)
        if formatted_trains:
            update_live_core_engine(formatted_trains)
        time.sleep(12)

# ----------------------------------------------------
# 6. 安全啟動與路由接口 (兼顧前端地圖與數據後台)
# ----------------------------------------------------
THREAD_STARTED = False

@app.before_request
def start_background_threads():
    global THREAD_STARTED
    if not THREAD_STARTED:
        with LOCK:
            if not THREAD_STARTED:
                t = threading.Thread(target=mtr_api_fetcher_thread, daemon=True)
                t.start()
                THREAD_STARTED = True

@app.route('/')
def map_page(): return render_template('map.html')

@app.route('/admin')
def admin_page(): return render_template('admin.html')

@app.route('/api/train-positions')
@app.route('/api/admin/dashboard')
def unified_api():
    line_filter = request.args.get('line', 'TWL').upper()
    with LOCK:
        up_count = sum(1 for t in ACTIVE_TRAINS.values() if t['line'] == line_filter and t['direction'] == 'UP')
        down_count = sum(1 for t in ACTIVE_TRAINS.values() if t['line'] == line_filter and t['direction'] == 'DOWN')
        
        train_positions = []
        for tid, t in ACTIVE_TRAINS.items():
            if t['line'] == line_filter:
                train_positions.append({
                    "id": tid, "line": t['line'], "from": t['from_sta'], "to": t['to_sta'],
                    "from_sta": t['from_sta'], "to_sta": t['to_sta'],
                    "direction": t['direction'], "ratio": round(t['ratio'], 3), "status": t['status'],
                    "dest": t['dest'], "lat": t['current_latlng'][0], "lng": t['current_latlng'][1]
                })
                
    return jsonify({
        "status": "success", "line": line_filter, "up_running": up_count, "down_running": down_count,
        "peak_use_today": PEAK_TRAIN_COUNT, "active_trains": train_positions, "trains": train_positions
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
