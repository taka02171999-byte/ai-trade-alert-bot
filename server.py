# server.py — AIりんご式 本番用（雷⚡損切り＋Discord Embed＋クールダウン8秒）
import os, csv, json, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, Response
from threading import Lock

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "8"))  # ← ここで秒数調整可

LOG_DIR = Path("logs")
CSV_PATH = LOG_DIR / "signals.csv"
LOG_DIR.mkdir(parents=True, exist_ok=True)

CSV_COLUMNS = ["ts","symbol","side","o","h","l","c","v","vwap","atr","tp","sl","tf","raw"]
if not CSV_PATH.exists():
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_COLUMNS).writeheader()

csv_lock = Lock()
last_alert_time = {}  # {(SYMBOL, side): unix_time}

def jst_now_iso():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).isoformat()

def _fmt(x): return "-" if x is None else str(x)

def _build_signal_embed(data: dict):
    side = (data.get("side") or "").lower()
    MAP = {
        "buy":  {"emoji":"🟢","title":"買いシグナル","color":0x2ecc71},
        "sell": {"emoji":"🔴","title":"売りシグナル","color":0xe74c3c},
        "tp":   {"emoji":"🎯","title":"利確シグナル","color":0x3498db},
        "sl":   {"emoji":"⚡","title":"⚡緊急損切りシグナル⚡","color":0xff0000},
    }
    meta = MAP.get(side, {"emoji":"📈","title":"シグナル","color":0x95a5a6})
    embed = {
        "title": f"{meta['emoji']} {meta['title']}",
        "color": meta["color"],
        "timestamp": jst_now_iso(),
        "fields": [
            {"name":"銘柄","value":_fmt(data.get("symbol")),"inline":True},
            {"name":"時間足","value":_fmt(data.get("tf")),"inline":True},
            {"name":"時刻","value":_fmt(data.get("time")),"inline":False},
            {"name":"価格(O/H/L/C)","value":f"{_fmt(data.get('o'))}/{_fmt(data.get('h'))}/{_fmt(data.get('l'))}/**{_fmt(data.get('c'))}**","inline":False},
            {"name":"出来高 / VWAP / ATR","value":f"{_fmt(data.get('v'))} / {_fmt(data.get('vwap'))} / {_fmt(data.get('atr'))}","inline":False},
        ],
        "footer":{"text":"AIりんご式"}
    }
    return {"embeds":[embed]}

def _post_discord(payload):
    if not DISCORD_WEBHOOK:
        print("[warn] Discord webhook未設定。通知スキップ。")
        return
    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        print(f"[info] Discord通知 status={r.status_code}")
    except Exception as e:
        print(f"[error] Discord通知失敗: {e}")

def append_csv_row(data):
    row = {
        "ts": jst_now_iso(),
        "symbol": data.get("symbol"),
        "side": (data.get("side") or "").lower(),
        "o": data.get("o"), "h": data.get("h"), "l": data.get("l"), "c": data.get("c"),
        "v": data.get("v"), "vwap": data.get("vwap"), "atr": data.get("atr"),
        "tp": data.get("tp"), "sl": data.get("sl"), "tf": data.get("tf"),
        "raw": json.dumps(data, ensure_ascii=False),
    }
    with csv_lock:
        with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_COLUMNS).writerow(row)

def should_send(symbol: str, side: str) -> bool:
    import time
    key = ((symbol or "").upper(), (side or "").lower())
    now = time.time()
    last = last_alert_time.get(key, 0.0)
    if now - last < COOLDOWN_SEC:
        print(f"[cooldown-skip] {key} {now-last:.2f}s < {COOLDOWN_SEC}s")
        return False
    last_alert_time[key] = now
    return True

app = Flask(__name__)

@app.get("/")
def root(): return "ok"

@app.get("/signals")
def get_signals():
    if not CSV_PATH.exists():
        return Response("", mimetype="text/csv")
    return Response(CSV_PATH.read_text("utf-8"), mimetype="text/csv")

@app.route("/webhook", methods=["GET","POST"])
def webhook():
    # GET /webhook?ping=1 → 通知しない・動作確認のみ
    if request.method == "GET" and request.args.get("ping"):
        return jsonify({"ok": True, "ping": True})

    try:
        d = request.get_json(silent=True) or {}
    except Exception:
        d = {}

    # CSVは常に記録（学習のため）
    append_csv_row(d)

    # Discord通知はクールダウンで制御
    side   = (d.get("side") or d.get("signal") or "").lower()
    symbol = (d.get("symbol") or d.get("ticker") or "UNKNOWN")
    if side and should_send(symbol, side):
        _post_discord(_build_signal_embed(d))
    else:
        print(f"[info] skip discord: side={side}")

    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
