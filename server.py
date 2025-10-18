# server.py (完全版)
from flask import Flask, request, jsonify, send_file
import os, json, requests, csv
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ====== 設定 ======
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")  # Renderの環境変数で設定済み
app = Flask(__name__)

# ログ保存先（Freeプランは再デプロイで消える可能性あり。まずは動作優先）
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "signals.csv"

PARAMS_FILE = Path("params.json")  # 学習で毎晩更新される想定（なければデフォルト使用）


# ====== 共通ユーティリティ ======
def jst_now_text():
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=9))
    ).strftime("%Y-%m-%d %H:%M:%S JST")

def read_params_for(symbol: str):
    """銘柄ごとのATR倍率（SL/TP）をparams.jsonから取得。無ければデフォルト"""
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
        print("!!! DISCORD_WEBHOOK missing")
        return
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

def log_signal(row: dict):
    headers = [
        "time","symbol","side","tf",
        "o","h","l","c","v","vwap","atr",
        "entry","tp","sl"
    ]
    new_file = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        if new_file:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in headers})
    print(">>> logged to CSV:", row)


# ====== ルート ======
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
    try:
        d = request.get_json(force=True, silent=True) or {}
        print(">>> incoming payload:", d)

        # 受信フォーマットの揺れを吸収
        symbol = (d.get("symbol") or d.get("ticker") or "UNKNOWN")
        side   = (d.get("side") or d.get("signal") or "buy").lower()
        tf     = d.get("tf") or d.get("timeframe") or ""
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

        # メッセージが完全に空の通常アラートも来ることがある
        if d == {}:
            post_discord_embed(
                "🔔 TradingViewから受信",
                "メッセージが空（通常アラート）。Pineの alert() か、メッセージ欄にJSONを設定してください。",
                [],
                color=0x3498DB
            )
            return jsonify({"ok": True})

        # params.jsonから銘柄別の係数を取得してSL/TPを計算
        coeff = read_params_for(symbol)
        sl_atr = float(coeff.get("sl_atr", 0.9))
        tp_atr = float(coeff.get("tp_atr", 1.7))
        entry  = c
        sl     = entry - atr * sl_atr if side == "buy" else entry + atr * sl_atr
        tp     = entry + atr * tp_atr if side == "buy" else entry - atr * tp_atr

        # CSVへ保存
        log_row = {
            "time": d.get("time") or jst_now_text(),
            "symbol": symbol,
            "side": side,
            "tf": tf,
            "o": o, "h": h, "l": l, "c": c, "v": v,
            "vwap": vwap, "atr": atr,
            "entry": entry, "tp": tp, "sl": sl,
        }
        try:
            log_signal(log_row)
        except Exception as e:
            print("!!! log error:", e)

        # Discordへ通知
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
        # 解析しやすいよう一旦200
        return jsonify({"ok": False, "error": str(e)}), 200
