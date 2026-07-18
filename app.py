import os
import time
import requests
import threading
from flask import Flask, jsonify, render_template

app = Flask(__name__)

# ====================== 配置 ======================
TWL_ORDER = ["CEN", "ADM", "TST", "JOR", "YMT", "MOK", "PRE", "SSP", "CSW", "LCK", "MEF", "LAK", "KWF", "KWH", "TWH", "TSW"]

# ====================== Live API ======================
@app.route('/api/live')
def get_live_trains():
    t = time.time()
    return {
        "TWL-UP-1": {"line": "TWL", "direction": "UP", "from": "CEN", "to": "TSW", "progress": (t % 40) / 40, "dest": "荃灣"},
        "TWL-UP-2": {"line": "TWL", "direction": "UP", "from": "ADM", "to": "TSW", "progress": ((t + 13) % 40) / 40, "dest": "荃灣"},
        "TWL-DOWN-1": {"line": "TWL", "direction": "DOWN", "from": "TSW", "to": "CEN", "progress": ((t + 25) % 40) / 40, "dest": "中環"},
    }

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
            print("Keep-Alive ping 成功")
        except:
            print("Keep-Alive ping 失敗")
        time.sleep(240)  # 每4分鐘 ping 一次

threading.Thread(target=keep_alive, daemon=True).start()

# ====================== 路由 ======================
@app.route('/')
def index():
    return render_template('map.html')

@app.route('/admin')
def admin():
    return render_template('admin.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)