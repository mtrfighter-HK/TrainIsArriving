import os
import time
from flask import Flask, jsonify, render_template

app = Flask(__name__)

# ====================== 配置 ======================
TWL_ORDER = ["CEN", "ADM", "TST", "JOR", "YMT", "MOK", "PRE", "SSP", "CSW", "LCK", "MEF", "LAK", "KWF", "KWH", "TWH", "TSW"]

# ====================== Live API ======================
@app.route('/api/live')
def get_live_trains():
    # 動態 progress，讓紅點移動
    t = time.time()
    return {
        "TWL-UP-1": {"line": "TWL", "direction": "UP", "from": "CEN", "to": "TSW", "progress": (t % 30) / 30, "dest": "荃灣"},
        "TWL-UP-2": {"line": "TWL", "direction": "UP", "from": "ADM", "to": "TSW", "progress": ((t + 10) % 30) / 30, "dest": "荃灣"},
        "TWL-DOWN-1": {"line": "TWL", "direction": "DOWN", "from": "TSW", "to": "CEN", "progress": ((t + 15) % 30) / 30, "dest": "中環"},
    }

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