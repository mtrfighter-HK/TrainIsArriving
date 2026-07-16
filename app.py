import time
import threading
import requests
from flask import Flask, jsonify, request, render_template

app = Flask(__name__, static_folder='.', static_url_path='')

# ==========================================
# 📊 全局數據存儲與配置
# ==========================================
ACTIVE_TRAINS = {}  # 儲存實時列車物理位置數據
PEAK_TRAINS_TODAY = {"TWL": 8, "TKL": 6}  # 當日最高用車量統計
LOCK = threading.Lock()

# 荃灣線 (TWL) 車站順序（由上行起點至終點）
TWL_ORDER = ["CEN", "ADM", "TST", "JOR", "YMT", "MOK", "PRE", "SSP", "CSW", "LCK", "MEF", "LAK", "KWF", "KWH", "TWH", "TSW"]

# 車站經緯度對照表（備用，若無法讀取 GeoJSON 時的基礎坐標）
STATION_COORDS = {
    "CEN": [22.28185, 114.1581], "ADM": [22.27945, 114.1641], "TST": [22.2989, 114.1719],
    "JOR": [22.3049, 114.1717],  "YMT": [22.3129, 114.1699],  "MOK": [22.3193, 114.1694],
    "PRE": [22.3256, 114.1687],  "SSP": [22.3307, 114.1623],  "CSW": [22.3350, 114.1575],
    "LCK": [22.3368, 114.1492],  "MEF": [22.3375, 114.1385],  "LAK": [22.3486, 114.1274],
    "KWF": [22.3568, 114.1317],  "KWH": [22.3646, 114.1313],  "TWH": [22.3707, 114.1281],
    "TSW": [22.3732, 114.1178]
}

# 站間純行車時間（秒數配置）
TRAVEL_TIME_CONFIG = {
    "CEN_ADM": 120, "ADM_TST": 180, "TST_JOR": 80,  "JOR_YMT": 80,
    "YMT_MOK": 80,  "MOK_PRE": 70,  "PRE_SSP": 90,  "SSP_CSW": 80,
    "CSW_LCK": 80,  "LCK_MEF": 90,  "MEF_LAK": 110, "LAK_KWF": 100,
    "KWF_KWH": 90,  "KWH_TWH": 90,  "TWH_TSW": 120
}

# ==========================================
# 🧮 物理引擎：經緯度插值計算
# ==========================================
def interpolate_coords(from_code, to_code, ratio):
    """根據兩站經緯度與比例 (0.0 - 1.0)，計算目前列車的經緯度"""
    p1 = STATION_COORDS.get(from_code)
    p2 = STATION_COORDS.get(to_code)
    if not p1 or not p2:
        return 22.321, 114.170 # 預設中心點（油麻地附近）
    
    # 限制比例在 0.0 到 1.0 之間
    r = max(0.0, min(1.0, ratio))
    lat = p1[0] + (p2[0] - p1[0]) * r
    lng = p1[1] + (p2[1] - p1[1]) * r
    return lat, lng

# ==========================================
# 📡 港鐵 API 數據獲取與流化引擎
# ==========================================
def fetch_mtr_data():
    """高頻安全輪詢港鐵 API 並將其轉化為物理行駛狀態"""
    global ACTIVE_TRAINS
    while True:
        try:
            url = "https://rt.mtr.com.hk/tickets/bycategory.html?category=TRN&line=TWL"
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                if data.get("status") == 1:
                    raw_trains = data.get("results", [])
                    update_train_physics(raw_trains)
        except Exception as e:
            print(f"[API 錯誤] 無法獲取港鐵數據: {e}")
        time.sleep(12)  # 每 12 秒向官方 API 更新一次數據

def update_train_physics(raw_trains):
    """根據 API 返回的 ttnt 倒數，更新或初始化 ACTIVE_TRAINS 字典"""
    global ACTIVE_TRAINS
    with LOCK:
        current_time = time.time()
        active_ids = set()

        for train in raw_trains:
            line = train.get("line", "TWL").upper()
            if line != "TWL": 
                continue

            station = train.get("station", "").upper()
            direction = train.get("direction", "").upper()
            dest = train.get("dest", "").upper()
            ttnt = int(train.get("ttnt", 99))

            # 尋找前一個車站
            idx = TWL_ORDER.index(station) if station in TWL_ORDER else -1
            if idx == -1: 
                continue

            # 定義行車方向的上一站與下一站
            if direction == "UP": # 往荃灣方向
                if idx == 0: continue # 中環沒有上一站
                from_sta = TWL_ORDER[idx - 1]
                to_sta = station
            else: # DOWN，往中環方向
                if idx == len(TWL_ORDER) - 1: continue # 荃灣沒有上一站
                from_sta = TWL_ORDER[idx + 1]
                to_sta = station

            train_id = f"{line}_{direction}_{from_sta}_{to_sta}"
            active_ids.add(train_id)

            # 取得站間總行駛時間
            time_key = f"{from_sta}_{to_sta}" if direction == "UP" else f"{to_sta}_{from_sta}"
            total_duration = TRAVEL_TIME_CONFIG.get(time_key, 110)

            # 計算進度比例 ratio
            if ttnt == 0:
                # 已進站停靠
                ratio = 1.0
                status = "stopped_at_station"
            elif ttnt == 1:
                # 剩餘不到 1 分鐘，預估已行駛了總長度的後半段
                elapsed = max(0, total_duration - 45)
                ratio = elapsed / total_duration
                status = "cruising"
            else:
                # ttnt >= 2，剛出發
                ratio = 0.1
                status = "cruising"

            lat, lng = interpolate_coords(from_sta, to_sta, ratio)

            # 更新或寫入全域變數
            ACTIVE_TRAINS[train_id] = {
                "id": train_id,
                "line": line,
                "direction": direction,
                "from_sta": from_sta,
                "to_sta": to_sta,
                "from": from_sta,  # 雙向兼容前端命名
                "to": to_sta,      # 雙向兼容前端命名
                "dest": dest,
                "ratio": ratio,
                "lat": lat,
                "lng": lng,
                "status": status,
                "last_update": current_time
            }

        # 🎯 自動清除超過 2 分鐘沒有在官方 API 出現的過期火車
        expired_ids = [tid for tid, t in ACTIVE_TRAINS.items() if current_time - t["last_update"] > 120]
        for tid in expired_ids:
            ACTIVE_TRAINS.pop(tid, None)

# ==========================================
# 🕒 後台物理引擎：每秒平滑前進 (Dead Reckoning)
# ==========================================
def smooth_movement_loop():
    """背景物理引擎，每 1 秒讓行行駛中的火車前進"""
    global ACTIVE_TRAINS
    while True:
        with LOCK:
            for train_id, train in list(ACTIVE_TRAINS.items()):
                if train["status"] == "cruising" and train["ratio"] < 1.0:
                    # 每秒讓進度微幅增加（假設全路段均勻行駛）
                    time_key = f"{train['from_sta']}_{train['to_sta']}" if train["direction"] == "UP" else f"{train['to_sta']}_{train['from_sta']}"
                    total_duration = TRAVEL_TIME_CONFIG.get(time_key, 110)
                    
                    # 增加比例
                    step = 1.0 / total_duration
                    new_ratio = min(1.0, train["ratio"] + step)
                    
                    # 更新比例與經緯度
                    train["ratio"] = new_ratio
                    train["lat"], train["lng"] = interpolate_coords(train["from_sta"], train["to_sta"], new_ratio)
                    
                    if new_ratio >= 1.0:
                        train["status"] = "stopped_at_station"
        time.sleep(1)

# ==========================================
# 🔌 前後端 API 路由對接門戶
# ==========================================
@app.route('/')
def index():
    return render_template('map.html')

@app.route('/admin')
def admin_page():
    return render_template('admin.html')

# 🎯 提供給 map.html (地圖列車位置)
@app.route('/api/train-positions')
def get_train_positions():
    line = request.args.get('line', 'TWL').upper()
    line_trains = [t for t in ACTIVE_TRAINS.values() if t["line"] == line]
    return jsonify({
        "status": "success",
        "trains": line_trains
    })

# 🎯 提供給 admin.html (直立式看板後台)
@app.route('/api/admin/dashboard')
def admin_dashboard():
    line = request.args.get('line', 'TWL').upper()
    
    up_count = 0
    down_count = 0
    line_trains = []
    
    for t_id, train in ACTIVE_TRAINS.items():
        if train.get('line') == line:
            line_trains.append(train)
            if train.get('direction') == 'UP':
                up_count += 1
            elif train.get('direction') == 'DOWN':
                down_count += 1
                
    # 動態更新今日峰值
    if len(line_trains) > PEAK_TRAINS_TODAY.get(line, 0):
        PEAK_TRAINS_TODAY[line] = len(line_trains)

    return jsonify({
        "up_running": up_count,
        "down_running": down_count,
        "peak_use_today": PEAK_TRAINS_TODAY.get(line, 8),
        "active_trains": line_trains,  # 完美對接 admin.html
        "trains": line_trains          # 兼容舊版命名
    })

# ==========================================
# 🚀 系統啟動
# ==========================================
if __name__ == '__main__':
    # 啟動 API 輪詢執行緒
    t1 = threading.Thread(target=fetch_mtr_data, daemon=True)
    t1.start()

    # 啟動每秒平滑位移物理引擎
    t2 = threading.Thread(target=smooth_movement_loop, daemon=True)
    t2.start()

    # 啟動 Flask 網頁伺服器
    app.run(host='0.0.0.0', port=5000, debug=True)
