# server.py — AIりんご式 受信/配信用 Flask サーバ
# - TradingView からの Webhook を受け取り（/signal）
# - CSV に追記して（logs/signals.csv）
# - Discord に即通知
# - CSV をダウンロード配布（/signals）
# Render では Start Command を:
#   gunicorn -w 1 -k gthread -b 0.0.0.0:$PORT server:app --timeout 120
# にしてください。

from flask import Flask, request, jsonify, send_file
from pathlib import Path
from datetime import datetime, timezone, timedelta
import os, csv, json, requests

app = Flask(__name__)

# ===== 環境変数 =====
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")  # 必須（無いと通知はスキップ）

# ===== パス・ファイル =====
BASE_DIR   = Path(__file__).resolve().parent
LOG_DIR    = BASE_DIR / "logs"
LOG_FILE   = LOG_DIR / "signals.csv"          # 受信シグナルの保存先
PARAMS_FILE = BASE_DIR / "params.json"        # 最適化で更新される係数（無くてもOK）

LOG_DIR.mkdir(exist_ok=True)

# ===== 共通ユーティリティ =====
def jst_now_text() -> str:
    """JSTの 'YYYY-MM-DD HH:MM:SS JST' 文字列"""
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=9))
    ).strftime("%Y-%m-%d %H:%M:%S JST")

def read_params_for(symbol: str) -> dict:
    """
    銘柄別 SL/TP のATR倍率を params.json から取得。
    無ければデフォルト（sl×0.9 / tp×1.7）
    """
    default = {"sl_atr": 0.9, "tp_atr": 1.7}
    try:
        if PARAMS_FILE.exists():
            data = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data.get(symbol, default)
    except Exception as e:
        print("params.json read error:", e)
    return default

def post_discord_embed(title: str, description: str, fields=None, color: int = 0x2ECC71):
    """Discord に埋め込みで通知（Webhook未設定ならスキップ）"""
    if not DISCORD_WEBHOOK:
        print("no DISCORD_WEBHOOK -> skip discord")
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
        print("discord status:", r.status_code)
    except Exception as e:
        print("discord error:", e)

def log_signal(row: dict):
    """CSVに1行追記。初回はヘッダを書き出し。"""
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
    print("logged:", row)

# ===== ルート =====
@app.route("/")
def root():
    return "ok"

@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "time": jst_now_text()})

@app.route("/signals", methods=["GET"])
def download_signals():
    """
    収集した CSV をそのまま配布。
    ※ optimizer（夜間学習）が HTTP で取りに来る想定
    """
    if LOG_FILE.exists():
        return send_file(LOG_FILE, as_attachment=True, download_name="signals.csv")
    return "no data yet", 200

@app.route("/signal", methods=["POST"])
def signal():
    """
    TradingView Webhook 受信口。
    受け取りフォーマットは柔軟に吸収：
      {
        "symbol": "USDJPY",     # or "ticker"
        "type": "buy|sell|tp|sl",   # or "side"
        "price": 150.12,        # or "c" / "close"
        "atr": 0.25,
        "tf": "5",              # or "timeframe" / "interval"
        "o":..., "h":..., "l":..., "c":..., "v":..., "vwap":...
      }
    """
    try:
        d = request.get_json(force=True, silent=True) or {}
    except Exception:
        d = {}

    print("incoming:", d)

    # 対応キーを吸収
    symbol = (d.get("symbol") or d.get("ticker") or "UNKNOWN")
    side   = (d.get("type") or d.get("side") or "buy").lower()
    tf     =  d.get("tf") or d.get("timeframe") or d.get("interval") or ""

    def f(x, default=0.0):
        try: return float(x)
        except: return default

    # 価格群
    o    = f(d.get("o"))
    h    = f(d.get("h"))
    l    = f(d.get("l"))
    c    = f(d.get("c") or d.get("close") or d.get("price"))
    vwap = f(d.get("vwap"))
    atr  = f(d.get("atr"))
    v    = d.get("v") or d.get("volume") or ""

    # params.json から係数を取って SL/TP を計算
    coeff  = read_params_for(symbol)
    slx = float(coeff.get("sl_atr", 0.9))
    tpx = float(coeff.get("tp_atr", 1.7))

    entry = c
    if side == "buy":
        sl = entry - atr * slx
        tp = entry + atr * tpx
    elif side == "sell":
        sl = entry + atr * slx
        tp = entry - atr * tpx
    else:
        # tp/sl 通知など種別が既定外の時は、そのまま値を通す
        sl = d.get("sl")
        tp = d.get("tp")

    # CSVへ保存
    row = {
        "time": d.get("time") or jst_now_text(),
        "symbol": symbol,
        "side": side,
        "tf": tf,
        "o": o, "h": h, "l": l, "c": c, "v": v,
        "vwap": vwap, "atr": atr,
        "entry": entry, "tp": tp, "sl": sl
    }
    try:
        log_signal(row)
    except Exception as e:
        print("csv log error:", e)

    # Discordへ通知
    title_map = {
        "buy":  "🟢 買いシグナル",
        "sell": "🔴 売りシグナル",
        "tp":   "💰 利確サイン",
        "sl":   "⚠️ 損切りサイン"
    }
    color_map = {
        "buy":  0x2ECC71,
        "sell": 0xE74C3C,
        "tp":   0xF1C40F,
        "sl":   0xE67E22
    }
    fields = [
        {"name":"足",   "value": tf or "-",          "inline": True},
        {"name":"終値", "value": f"{c:.4f}",         "inline": True},
        {"name":"ATR",  "value": f"{float(atr):.4f}","inline": True},
        {"name":"利確", "value": f"{tp if tp is None else f'{float(tp):.4f}'}", "inline": True},
        {"name":"損切り","value": f"{sl if sl is None else f'{float(sl):.4f}'}","inline": True},
    ]
    desc = f"銘柄: **{symbol}**\n時刻: {jst_now_text()}"
    post_discord_embed(
        title_map.get(side, "📈 シグナル"),
        desc,
        fields=fields,
        color=color_map.get(side, 0x3498DB)
    )

    return jsonify({"ok": True})
    
# ===== ローカル実行用（Render本番ではGunicornが使う） =====
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
