# server.py — AIりんご式 本番用（雷⚡損切り + Discord Embed対応）
# 依存: flask, requests

import os, csv, json, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, Response
from threading import Lock

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")

LOG_DIR = Path("logs")
CSV_PATH = LOG_DIR / "signals.csv"
LOG_DIR.mkdir(parents=True, exist_ok=True)

CSV_COLUMNS = [
    "ts", "symbol", "side", "o", "h", "l", "c", "v",
    "vwap", "atr", "tp", "sl", "tf", "raw"
]
if not CSV_PATH.exists():
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_COLUMNS).writeheader()

csv_lock = Lock()

# === 共通関数 ===
def jst_now_iso():
    return datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=9))
    ).isoformat()

def _fmt(x): return "-" if x is None else str(x)

# === Discord通知（日本語 + 絵文字） ===
def _build_signal_embed(data: dict):
    side = (data.get("side") or "").lower()
    MAP = {
        "buy":  {"emoji": "🟢", "title": "買いシグナル",   "color": 0x2ecc71},
        "sell": {"emoji": "🔴", "title": "売りシグナル",   "color": 0xe74c3c},
        "tp":   {"emoji": "🎯", "title": "利確シグナル",   "color": 0x3498db},
        # ⚡雷エフェクト損切りシグナル
        "sl":   {"emoji": "⚡", "title": "⚡緊急損切りシグナル⚡", "color": 0xff0000},
    }
    meta = MAP.get(side, {"emoji": "📈", "title": "シグナル", "color": 0x95a5a6})

    embed = {
        "title": f"{meta['emoji']} {meta['title']}",
        "color": meta["color"],
        "timestamp": jst_now_iso(),
        "fields": [
            {"name": "銘柄", "value": _fmt(data.get("symbol")), "inline": True},
            {"name": "時間足", "value": _fmt(data.get("tf")), "inline": True},
            {"name": "時刻", "value": _fmt(data.get("time")), "inline": False},
            {"name": "価格(O/H/L/C)", "value": f"{_fmt(data.get('o'))}/{_fmt(data.get('h'))}/{_fmt(data.get('l'))}/**{_fmt(data.get('c'))}**", "inline": False},
            {"name": "出来高 / VWAP / ATR", "value": f"{_fmt(data.get('v'))} / {_fmt(data.get('vwap'))} / {_fmt(data.get('atr'))}", "inline": False},
        ],
        "footer": {"text": "AIりんご式"}
    }
    return {"embeds": [embed]}

def _post_discord(payload):
    if not DISCORD_WEBHOOK:
        print("[warn] Discord webhook未設定。通知スキップ。")
        return
    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
        print(f"[info] Discord通知 status={r.status_code}")
    except Exception as e:
        print(f"[error] Discord通知失敗: {e}")

def notify_from_tv(data):
    if data.get("ping") is True:
        _post_discord({"content": f"✅ Webhookテスト成功\n{jst_now_iso()}"})
        return
    _post_discord(_build_signal_embed(data))

def append_csv_row(data):
    row = {
        "ts": jst_now_iso(),
        "symbol": data.get("symbol"),
        "side": (data.get("side") or "").lower(),
        "o": data.get("o"),
        "h": data.get("h"),
        "l": data.get("l"),
        "c": data.get("c"),
        "v": data.get("v"),
        "vwap": data.get("vwap"),
        "atr": data.get("atr"),
        "tp": data.get("tp"),
        "sl": data.get("sl"),
        "tf": data.get("tf"),
        "raw": json.dumps(data, ensure_ascii=False),
    }
    with csv_lock:
        with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_COLUMNS).writerow(row)

app = Flask(__name__)

@app.get("/")
def root(): return "ok"

@app.get("/signals")
def get_signals():
    if not CSV_PATH.exists(): return Response("", mimetype="text/csv")
    return Response(CSV_PATH.read_text("utf-8"), mimetype="text/csv")

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET" and request.args.get("ping"):
        notify_from_tv({"ping": True})
        return jsonify({"ok": True, "ping": True})

    try:
        data = request.get_json(silent=True) or {}
    except Exception:
        data = {}
    append_csv_row(data)
    notify_from_tv(data)
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
