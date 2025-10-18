# server.py
from flask import Flask, request, jsonify, send_file
import os, json, csv, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

app = Flask(__name__)

# ====== 設定 ======
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"; LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "signals.csv"
PARAMS_FILE = BASE_DIR / "params.json"

JST = timezone(timedelta(hours=9))

def jst_now_text():
    return datetime.now(timezone.utc).astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")

def read_params_for(symbol: str):
    default = {"sl_atr": 0.9, "tp_atr": 1.7}
    try:
        if PARAMS_FILE.exists():
            data = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
            return data.get(symbol, default)
    except:
        pass
    return default

def post_discord_embed(title, description, fields=None, color=0x2ecc71):
    if not DISCORD_WEBHOOK:
        print("!!! DISCORD_WEBHOOK missing")
        return
    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "fields": fields or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "AIりんご式"}
        }]
    }
    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        print(">>> discord status:", r.status_code)
    except Exception as e:
        print("!!! discord error:", e)

def log_signal(row: dict):
    headers = ["time","symbol","side","tf","o","h","l","c","v","vwap","atr","entry","tp","sl"]
    new_file = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        if new_file:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in headers})
    print(">>> logged:", row)

@app.route("/")
def index():
    return "ok"

@app.route("/signals", methods=["GET"])
def download_signals():
    if LOG_FILE.exists():
        return send_file(LOG_FILE, as_attachment=True, download_name="signals.csv")
    return "no data yet", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    # TradingView からの JSON を受信
    d = request.get_json(force=True, silent=True) or {}
    print(">>> incoming:", d)

    symbol = (d.get("symbol") or d.get("ticker") or "UNKNOWN")
    side   = (d.get("side") or d.get("signal") or "buy").lower()
    tf     = d.get("tf") or d.get("timeframe") or ""
    # 数値化ヘルパ
    def f(x, default=0.0):
        try: return float(x)
        except: return default
    o = f(d.get("o"))
    h = f(d.get("h"))
    l = f(d.get("l"))
    c = f(d.get("c") or d.get("close") or d.get("price"))
    v = d.get("v") or d.get("volume") or ""
    vwap = f(d.get("vwap"))
    atr  = f(d.get("atr"))

    # params.json から銘柄別の SL/TP 係数を取得
    coeff = read_params_for(symbol)
    sl_k = float(coeff.get("sl_atr", 0.9))
    tp_k = float(coeff.get("tp_atr", 1.7))

    entry = c
    sl = entry - atr * sl_k if side == "buy" else entry + atr * sl_k
    tp = entry + atr * tp_k if side == "buy" else entry - atr * tp_k

    # CSV ログ
    log_row = {
        "time": d.get("time") or jst_now_text(),
        "symbol": symbol, "side": side, "tf": tf,
        "o": o, "h": h, "l": l, "c": c, "v": v,
        "vwap": vwap, "atr": atr, "entry": entry, "tp": tp, "sl": sl
    }
    try:
        log_signal(log_row)
    except Exception as e:
        print("!!! log error:", e)

    # Discord 通知
    dir_name = "買い" if side=="buy" else "売り"
    fields = [
        {"name":"足", "value": tf or "-", "inline": True},
        {"name":"終値", "value": f"{c:.2f}", "inline": True},
        {"name":"ATR", "value": f"{atr:.2f}", "inline": True},
        {"name":"利確", "value": f"{tp:.2f}", "inline": True},
        {"name":"損切り", "value": f"{sl:.2f}", "inline": True},
    ]
    post_discord_embed(
        "📈 買いシグナル" if side=="buy" else "📉 売りシグナル",
        f"銘柄: **{symbol}**\n方向: **{dir_name}**\n時刻: {jst_now_text()}",
        fields,
        color = 0x2ECC71 if side=="buy" else 0xE74C3C
    )
    return jsonify({"ok": True})

if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", "10000"))
    # ローカルで試すとき用。Render 本番では Gunicorn が使うのでここは実行されません。
    app.run(host="0.0.0.0", port=port)
