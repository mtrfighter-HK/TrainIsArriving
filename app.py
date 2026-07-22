import os
import time
import requests
import threading
from flask import Flask, jsonify, render_template

app = Flask(__name__)

# ====================== 配置 ======================
TWL_ORDER = ["CEN", "ADM", "TST", "JOR", "YMT", "MOK", "PRE", "SSP", "CSW", "LCK", "MEF", "LAK", "KWF", "KWH", "TWH", "TSW"]

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
    """🆕 新增：提供給後台 admin.html 統計上下行列車數量使用"""
    trains = get_current_trains()
    
    # 統計上行與下行數量
    up_count = sum(1 for t in trains.values() if t["direction"] == "UP")
    down_count = sum(1 for t in trains.values() if t["direction"] == "DOWN")
    
    return jsonify({
        "up_running": up_count,
        "down_running": down_count,
        "total_running": len(trains),
        "trains": list(trains.values())
    })

# ====================== 背景收集器 ======================
def background_collector():
    while True:
        try:
            for sta in TWL_ORDER:
                try:
                    url = f"https://rt.data.gov.hk/v1/transport/mtr/getSchedule.php?line=TWL&sta={sta}"
                    r = requests.get(url, timeout=8)
                    if r.status_code == 200:
                        print(f"收集 {sta} 數據成功")
                except:
                    pass
        except:
            pass
        time.sleep(60)

threading.Thread(target=background_collector, daemon=True).start()

# ====================== Keep-Alive ======================
def keep_alive():
    while True:
        try:
            requests.get("http://localhost:5000", timeout=5)
            requests.get("http://localhost:5000/api/live", timeout=5)
            print("Keep-Alive ping 成功")
        except:
            print("Keep-Alive ping 失敗")
        time.sleep(180)  # 每3分鐘 ping 一次

threading.Thread(target=keep_alive, daemon=True).start()

# ====================== 網頁視圖路由 ======================
@app.route('/')
def index():
    return render_template('map.html')

@app.route('/admin')
def admin():
    return render_template('admin.html')

@app.route('/data')
def data():
    html = """
    <html>
    <head><meta charset="UTF-8"><title>MTR 數據後台</title></head>
    <body style="font-family:Arial; padding:20px;">
        <h1>📊 MTR 數據後台</h1>
        <p><a href="/">← 返回地圖</a></p>
        <p><a href="/api/live">查看 Live API 數據</a></p>
        <p><a href="/api/admin/dashboard">查看 後台統計 API 數據</a></p>
    </body>
    </html>
    """
    return html

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
