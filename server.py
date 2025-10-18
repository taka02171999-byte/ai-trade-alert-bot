# server.py（統合・重複なし・安定版）
from flask import Flask, request, jsonify, send_file
import os, json, requests, csv
from pathlib import Path
from datetime import datetime, timezone, timedelta

app = Flask(__name__)

# ====== 環境変数 ======
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")  # Discord送信用
SIGNAL_TOKEN    = os.getenv("SIGNAL_TOKEN", "") # 任意: /signal の簡易認証

# ====== パス ======
LOG_DIR = Path("logs"); LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "signals.csv"
PARAMS_FILE = Path("params.json")

# ====== 共通ユーティリティ ======
def jst_now_text():
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=9))
    ).strftime("%Y-%m-%d %H:%M:%S JST")

def read_params_for(symbol: str):
    default = {"sl_atr": 0.9, "tp_atr": 1.7}
    try:
        if PARAMS_FILE.exists():
            data = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
            return data.get(symbol, default)
    except Exception:
        pass
    return default

def post_discord_embed(title, description, fields=None, color=0x2ECC71):
    if not DISCORD_WEBHOOK:
        print("!!! DISCORD_WEBHOOK missing"); return
    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "fields": fields or [],
            "footer": {"text": "AIりんご式"},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }]
    }
    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        print(">>> discord status:", r.status_code)
    except Exception as e:
        print("!!! discord error:", e)

# CSVは1つのヘッダに統一
CSV_HEADERS = ["time","symbol","side","tf","o","h","l","c","v","vwap","atr","entry","tp","sl"]

def append_csv(row: dict):
    new_file = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if new_file:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in CSV_HEADERS})
    print(">>> logged to CSV:", row)

# ====== ルート ======
@app.get("/")
def index():
    return "ok"

@app.get("/signals")
def download_signals():
    if LOG_FILE.exists():
        return send_file(LOG_FILE, as_attachment=True, download_name="signals.csv")
    return "no data yet", 200

# --- TradingViewのPine側（テンプレJSON）から受け取る ---
@app.post("/webhook")
def webhook():
    try:
        d = request.get_json(force=True, silent=True) or {}
        print(">>> /webhook payload:", d)

        # 揺れに強い取り出し
        symbol = (d.get("symbol") or d.get("ticker") or "UNKNOWN")
        side   = (d.get("side")   or d.get("signal") or "buy").lower()
        tf     = d.get("tf") or d.get("timeframe") or ""

        def f(x, default=0.0):
            try: return float(x)
            except: return default

        o = f(d.get("o")); h = f(d.get("h")); l = f(d.get("l"))
        c = f(d.get("c") or d.get("close") or d.get("price"))
        v = d.get("v") or d.get("volume") or ""
        vwap = f(d.get("vwap")); atr = f(d.get("atr"))

        # メッセージ空（通常アラート）の救済
        if d == {}:
            post_discord_embed(
                "🔔 TradingViewから受信",
                "メッセージが空（通常アラート）。Pineの alert() か、メッセージ欄にJSONを設定してください。",
                color=0x3498DB
            )
            return jsonify({"ok": True})

        # paramsからSL/TP計算
        coeff = read_params_for(symbol)
        sl_atr = float(coeff.get("sl_atr", 0.9))
        tp_atr = float(coeff.get("tp_atr", 1.7))
        entry  = c
        sl     = entry - atr * sl_atr if side == "buy" else entry + atr * sl_atr
        tp     = entry + atr * tp_atr if side == "buy" else entry - atr * tp_atr

        # CSV保存
        append_csv({
            "time": d.get("time") or jst_now_text(),
            "symbol": symbol, "side": side, "tf": tf,
            "o": o, "h": h, "l": l, "c": c, "v": v,
            "vwap": vwap, "atr": atr, "entry": entry, "tp": tp, "sl": sl
        })

        # Discord通知
        dir_name = "買い" if side == "buy" else "売り"
        fields = [
            {"name":"足", "value": tf or "-", "inline": True},
            {"name":"終値", "value": f"{c:.2f}", "inline": True},
            {"name":"ATR", "value": f"{atr:.2f}", "inline": True},
            {"name":"利確", "value": f"{tp:.2f}", "inline": True},
            {"name":"損切り", "value": f"{sl:.2f}", "inline": True},
        ]
        post_discord_embed(
            title = "📈 買いシグナル" if side=="buy" else "📉 売りシグナル",
            description = f"銘柄: **{symbol}**\n方向: **{dir_name}**\n時刻: {jst_now_text()}",
            fields = fields,
            color = 0x2ECC71 if side=="buy" else 0xE74C3C
        )
        print(">>> sent to Discord")
        return jsonify({"ok": True})
    except Exception as e:
        print("!!! webhook error:", e)
        return jsonify({"ok": False, "error": str(e)}), 200

# --- 汎用シグナル：Pineのalert()からJSON送る用（type=buy/sell/tp/sl） ---
@app.post("/signal")
def signal():
    # 任意の簡易認証（URL?token=xxx）
    token = request.args.get("token", "")
    if SIGNAL_TOKEN and token != SIGNAL_TOKEN:
        return "forbidden", 403

    d = request.get_json(force=True, silent=True) or {}
    print(">>> /signal payload:", d)

    symbol  = str(d.get("symbol", "UNKNOWN"))
    sigtype = str(d.get("type", "unknown")).lower()  # buy/sell/tp/sl
    price   = float(d.get("price", 0) or 0)
    atr     = float(d.get("atr", 0) or 0)
    tf      = str(d.get("tf", ""))

    # CSV形式に合わせて正規化（/signalは最小項目のみ）
    side = "buy" if sigtype in ("buy","tp") else "sell" if sigtype in ("sell","sl") else "unknown"
    entry = price
    # パラメ係数があればTP/SLを計算（なければ空欄でもOK）
    coeff = read_params_for(symbol)
    sl_atr = float(coeff.get("sl_atr", 0.9))
    tp_atr = float(coeff.get("tp_atr", 1.7))
    sl = entry - atr * sl_atr if side == "buy" else entry + atr * sl_atr
    tp = entry + atr * tp_atr if side == "buy" else entry - atr * tp_atr

    append_csv({
        "time": jst_now_text(), "symbol": symbol, "side": side, "tf": tf,
        "o":"", "h":"", "l":"", "c": price, "v":"", "vwap":"", "atr": atr,
        "entry": entry, "tp": tp, "sl": sl
    })

    title_map = {"buy":"🟢 買い","sell":"🔴 売り","tp":"💰 利確","sl":"⚠️ 損切り"}
    color_map = {"buy":0x2ecc71,"sell":0xe74c3c,"tp":0xf1c40f,"sl":0xe67e22}
    title = title_map.get(sigtype, "📈 シグナル")
    color = color_map.get(sigtype, 0x3498db)
    desc  = f"銘柄: {symbol}\n価格: {price}\nATR: {atr}\n足: {tf}\n受信: {jst_now_text()}"
    post_discord_embed(title, desc, color=color)

    return jsonify({"ok": True})

# ここで終わり（重複定義を置かない）
