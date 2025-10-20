# server.py — AIりんご式 本番安定版（トレード記録 + 日本語Discord）
# 依存: flask, requests
import os, csv, json, uuid, requests, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, Response
from threading import Lock

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")

# 保存先
LOG_DIR = Path("logs"); LOG_DIR.mkdir(parents=True, exist_ok=True)
CSV_SIGNALS = LOG_DIR / "signals.csv"    # 受信ログ（そのまま）
CSV_TRADES  = LOG_DIR / "trades.csv"     # 1トレード = Entry→Exit

# CSV初期化
if not CSV_SIGNALS.exists():
    with CSV_SIGNALS.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=[
            "ts","symbol","side","o","h","l","c","v","vwap","atr","tf","raw"
        ]).writeheader()

if not CSV_TRADES.exists():
    with CSV_TRADES.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=[
            "open_ts","close_ts","symbol","pos_id","side","entry","exit","pnl_pct","tf"
        ]).writeheader()

csv_lock = Lock()

def jst_now_iso():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).isoformat()

def to_f(x, default=None):
    try:
        return float(x)
    except:
        return default

# === 8秒デバウンス（同一シンボル・同一sideの連打抑制） ===
LAST_SENT = {}  # {(symbol, side): unix_ts}
DEBOUNCE_SEC = int(os.getenv("DEBOUNCE_SEC", "8"))

def pass_debounce(symbol, side):
    key = (symbol, side)
    now = time.time()
    last = LAST_SENT.get(key, 0)
    if now - last < DEBOUNCE_SEC:
        return False
    LAST_SENT[key] = now
    return True

# === アクティブポジション保持 {symbol: {"id":..., "side":..., "entry":..., "tf":...}} ===
ACTIVE_POS = {}

# === Discord ===
def post_discord(title, desc, color):
    if not DISCORD_WEBHOOK: return
    payload = {"embeds":[{
        "title": title, "description": desc, "color": color,
        "timestamp": jst_now_iso(), "footer":{"text":"AIりんご式"}
    }]}
    try:
        requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
    except Exception as e:
        print("discord error:", e)

# === ログ ===
def log_signal_row(d: dict):
    row = {
        "ts": jst_now_iso(),
        "symbol": d.get("symbol"), "side": d.get("side"),
        "o": d.get("o"), "h": d.get("h"), "l": d.get("l"), "c": d.get("c"),
        "v": d.get("v"), "vwap": d.get("vwap"), "atr": d.get("atr"),
        "tf": d.get("tf"), "raw": json.dumps(d, ensure_ascii=False)
    }
    with csv_lock:
        with CSV_SIGNALS.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=row.keys()).writerow(row)

def log_trade_row(open_ts, close_ts, symbol, pos_id, side, entry, exitp, pnl_pct, tf):
    with csv_lock:
        with CSV_TRADES.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=[
                "open_ts","close_ts","symbol","pos_id","side","entry","exit","pnl_pct","tf"
            ]).writerow({
                "open_ts": open_ts, "close_ts": close_ts, "symbol": symbol,
                "pos_id": pos_id, "side": side, "entry": entry, "exit": exitp,
                "pnl_pct": pnl_pct, "tf": tf
            })

# === Flask ===
app = Flask(__name__)

@app.get("/")
def root(): return "ok"

@app.get("/signals")
def get_signals():
    return Response(CSV_SIGNALS.read_text("utf-8"), mimetype="text/csv")

@app.get("/trades")
def get_trades():
    return Response(CSV_TRADES.read_text("utf-8"), mimetype="text/csv")

@app.route("/webhook", methods=["GET","POST"])
def webhook():
    # GET ?ping=1 は無通知(レスだけ)
    if request.method == "GET" and request.args.get("ping"):
        return jsonify({"ok": True, "ping": True})

    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or data.get("ticker") or "UNKNOWN").upper()
    side   = (data.get("side") or "").lower()
    tf     = data.get("tf") or data.get("timeframe") or "-"
    price  = to_f(data.get("c") or data.get("close"))

    # 受信ログは常に残す
    log_signal_row(data)

    # デバウンス（BUY/SELL/TP/SLのみ）
    if side in ("buy","sell","tp","sl") and not pass_debounce(symbol, side):
        return jsonify({"ok": True, "debounced": True})

    # === ハンドリング ===
    if side in ("buy","sell") and price is not None:
        pos_id = f"{symbol}-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        ACTIVE_POS[symbol] = {"id": pos_id, "side": side, "entry": price, "tf": tf, "open_ts": jst_now_iso()}
        title = "🟢 買いポジション開始" if side=="buy" else "🔴 売りポジション開始"
        desc  = f"銘柄: **{symbol}**\n時間足: {tf}\nエントリー価格: **{price}**\nポジションID: `{pos_id}`"
        post_discord(title, desc, 0x2ecc71 if side=="buy" else 0xe74c3c)
        return jsonify({"ok": True})

    if side in ("tp","sl") and price is not None:
        pos = ACTIVE_POS.get(symbol)
        pos_id = pos["id"] if pos else "N/A"
        entry  = pos["entry"] if pos else None
        opened = pos["open_ts"] if pos else "-"
        # PnL%
        pnl_pct = None
        if entry is not None:
            if pos and pos.get("side")=="buy":
                pnl_pct = (price/entry - 1.0) * 100.0
            elif pos and pos.get("side")=="sell":
                pnl_pct = (entry/price - 1.0) * 100.0
        # 記録
        log_trade_row(opened, jst_now_iso(), symbol, pos_id, pos["side"] if pos else "-", entry, price, None if pnl_pct is None else round(pnl_pct,3), tf)
        # 通知
        if side=="tp":
            post_discord("🎯 利確", f"銘柄: **{symbol}**\n時間足: {tf}\n約定価格: **{price}**\nエントリー: {entry}\nPnL: **{pnl_pct:.2f}%**\nポジションID: `{pos_id}`", 0x3498db)
        else:
            post_discord("⚡ 損切り", f"銘柄: **{symbol}**\n時間足: {tf}\n約定価格: **{price}**\nエントリー: {entry}\nPnL: **{pnl_pct:.2f}%**\nポジションID: `{pos_id}`", 0xffc107)
        # クローズ
        ACTIVE_POS.pop(symbol, None)
        return jsonify({"ok": True})

    # その他は軽く通知
    if side:
        post_discord("📈 シグナル", f"銘柄: **{symbol}** / 種別: {side} / 価格: {price}\nTF: {tf}", 0x95a5a6)
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.getenv("PORT","10000"))
    app.run(host="0.0.0.0", port=port)
