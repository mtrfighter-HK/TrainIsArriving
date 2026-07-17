import os
import json
import time
import math
import requests
import threading
from flask import Flask, jsonify, render_template

app = Flask(__name__)

# ====================== 配置 ======================
TWL_ORDER = ["CEN", "ADM", "TST", "JOR", "YMT", "MOK", "PRE", "SSP", "CSW", "LCK", "MEF", "LAK", "KWF", "KWH", "TWH", "TSW"]

# 全局數據
TRACK_FEATURES = []
TRAVEL_TIME_CONFIG = {}
ACTIVE_TRAINS = {}
LOCK = threading.Lock()

# ====================== 空間計算 ======================
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

# ====================== 數據加載 ======================
def load_base_files():
    global TRACK_FEATURES, TRAVEL_TIME_CONFIG
    print("🚀 加載基礎檔...")

    # 軌道
    try:
        with open('static/路線軌道檔/Track.TsuenWanLine.geojson', 'r', encoding='utf-8') as f:
            data = json.load(f)
            TRACK_FEATURES.extend(data.get("features", []))
    except:
        print("⚠️ 軌道檔加載失敗")

    # 時間配置
    try:
        with open('static/系統基本檔/TravelTimeConfig.json', 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            for t in cfg.get("travel_times", []):
                key = f"{t['from'].upper()}_{t['to'].upper()}"
                TRAVEL_TIME_CONFIG[key] = t.get("run_time_sec") or t.get("duration_sec") or 110
    except:
        print("⚠️ 時間配置加載失敗")

load_base_files()

# ====================== 背景收集器 ======================
def background_collector():
    while True:
        try:
            stations = TWL_ORDER
            for sta in stations:
                try:
                    url = f"https://rt.data.gov.hk/v1/transport/mtr/getSchedule.php?line=TWL&sta={sta}"
                    r = requests.get(url, timeout=8)
                    if r.status_code == 200:
                        # 這裡可以存入資料庫或處理數據
                        pass
                except:
                    pass
        except:
            pass
        time.sleep(60)

threading.Thread(target=background_collector, daemon=True).start()

# ====================== API ======================
@app.route('/api/live')
def get_live_trains():
    # 暫時返回模擬數據（之後改成真實計算）
    return {
        "TWL-UP-1": {"line": "TWL", "direction": "UP", "from": "CEN", "to": "TSW", "progress": 0.25, "dest": "荃灣"},
        "TWL-UP-2": {"line": "TWL", "direction": "UP", "from": "ADM", "to": "TSW", "progress": 0.65, "dest": "荃灣"},
        "TWL-DOWN-1": {"line": "TWL", "direction": "DOWN", "from": "TSW", "to": "CEN", "progress": 0.45, "dest": "中環"},
    }

@app.route('/')
def index():
    return render_template('map.html')

@app.route('/admin')
def admin():
    return render_template('admin.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)