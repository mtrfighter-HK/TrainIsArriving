import os
import time
import requests
import threading
from flask import Flask, jsonify, render_template

app = Flask(__name__)

# ====================== 配置 ======================
TWL_ORDER = ["CEN", "ADM", "TST", "JOR", "YMT", "MOK", "PRE", "SSP", "CSW", "LCK", "MEF", "LAK", "KWF", "KWH", "TWH", "TSW"]

# 🆕 新增：用來暫存真實車站班次資料的字典
STATION_DATA = {}

# ====================== 核心數據邏輯 ======================
def get_current_trains():
    """產生實時（目前為模擬）的列車數據，供地圖與後台共同使用"""
    t = time.time()
    return {
        "TWL-UP-1": {"line": "TWL", "direction": "UP", "from": "CEN", "to": "TSW", "progress": (t % 40) / 40, "dest": "荃灣"},
        "TWL-UP-2": {"line": "TWL", "direction": "UP", "from": "ADM", "to": "TSW", "progress": ((t + 13) % 40) / 40, "dest": "荃灣"},
        "TWL-DOWN-1": {"line": "TWL", "direction": "DOWN", "from": "TSW", "to": "CEN", "progress": ((t + 25) % 40) / 40, "dest": "中環"},
    }

# ====================== API 路由 ======================
@app.route('/api/live')
def get_live_trains():
    """提供給前端 map.html 畫火車點使用"""
    return jsonify(get_current_trains())

@app.route('/api/admin/dashboard')
def admin_dashboard():
    """提供給後台 admin.html 統計上下行列車數量使用"""
    trains = get_current_trains()
    up_count = sum(1 for t in trains.values() if t["direction"] == "UP")
    down_count = sum(1 for t in trains.values() if t["direction"] == "DOWN")
    
    return jsonify({
        "up_running": up_count,
        "down_running": down_count,
        "total_running": len(trains),
        "trains": list(trains.values())
    })

@app.route('/api/station/<sta>')
def get_station_data(sta):
    """🆕 新增：當前端點擊車站時，回傳該站最新的真實到站資料"""
    # 根據車站代碼 (如 PRE) 取得儲存的資料，如果沒有就回傳空字典
    return jsonify(STATION_DATA.get(sta.upper(), {}))

# ====================== 背景收集器 ======================
def background_collector():
    global STATION_DATA
    while True:
        try:
            for sta in TWL_ORDER:
                try:
                    url = f"https://rt.data.gov.hk/v1/transport/mtr/getSchedule.php?line=TWL&sta={sta}"
                    r = requests.get(url, timeout=8)
                    if r.status_code == 200:
                        data = r.json()
                        # 🆕 新增：解析港鐵 API，把 UP 和 DOWN 的班次資料存起來
                        if data.get("status") == 1:
                            # 港鐵 API 的資料通常放在 data["data"]["TWL-PRE"] 這樣的結構裡
                            STATION_DATA[sta] = data.get("data", {}).get(f"TWL-{sta}", {})
                        print(f"收集 {sta} 數據成功")
                except:
                    pass
        except:
            pass
        # 避免被官方 API 封鎖，這裡設為每 60 秒更新一次所有車站
        time.sleep(60) 

threading.Thread(target=background_collector, daemon=True).start()

# ====================== Keep-Alive ======================
def keep_alive():
    while True:
        try:
            requests.get("http://localhost:5000", timeout=5)
            requests.get("http://localhost:5000/api/live", timeout=5)
        except:
            pass
        time.sleep(180)

threading.Thread(target=keep_alive, daemon=True).start()

# ====================== 網頁視圖路由 ======================
@app.route('/')
def index():
    return render_template('map.html')

@app.route('/admin')
def admin():
    return render_template('admin.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
