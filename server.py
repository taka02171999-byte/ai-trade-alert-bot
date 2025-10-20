# server.py — Webhook 1本・fixed/rt 内部併走 + 勝者のみ通知（日本語Discord / トレード記録 / 8秒ガード）
# 依存: flask, requests
import os, csv, json, uuid, requests, time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, Response
from threading import Lock

# ====== 設定（環境変数）======
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
AGENTS = [a.strip().lower() for a in os.getenv("AGENTS", "fixed,rt").split(",") if a.strip()]
if not AGENTS: AGENTS = ["fixed"]  # フォールバック
DEBOUNCE_SEC = int(os.getenv("DEBOUNCE_SEC", "8"))
BEST_AGENT_MODE = os.getenv("BEST_AGENT_MODE", "on").lower()  # "on" なら勝者のみ通知
DEFAULT_AGENT = os.getenv("DEFAULT_AGENT", "fixed")           # 勝者ファイル無い時の初期値

# ====== 永続ファイル ======
LOG_DIR = Path("logs"); LOG_DIR.mkdir(parents=True, exist_ok=True)
CSV_SIGNALS = LOG_DIR / "signals.csv"   # 受信ログ
CSV_TRADES  = LOG_DIR / "trades.csv"    # トレード（Entry→Exit）
ACTIVE_AGENT_FILE = Path("active_agent.txt")  # 比較スクリプトが毎晩更新

# CSV初期化（agent列あり）
if not CSV_SIGNALS.exists():
    with CSV_SIGNALS.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=[
            "ts","agent","symbol","side","o","h","l","c","v","vwap","atr","tf","raw"
        ]).writeheader()

if not CSV_TRADES.exists():
    with CSV_TRADES.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=[
            "open_ts","close_ts","agent","symbol","pos_id","side","entry","exit","pnl_pct","tf"
        ]).writeheader()

csv_lock = Lock()
LAST_SENT = {}  # {(agent,symbol,side): ts}
ACTIVE_POS = {} # {agent: {symbol: {...}}}

# ====== 共通関数 ======
def jst_now_iso():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).isoformat()

def to_f(x, default=None):
    try: return float(x)
    except: return default

def pass_debounce(agent, symbol, side):
    key = (agent, symbol, side)
    now = time.time()
    last = LAST_SENT.get(key, 0)
    if now - last < DEBOUNCE_SEC:
        return False
    LAST_SENT[key] = now
    return True

def current_active_agent():
    """勝者ファイルがある→その値 / 無い→DEFAULT_AGENT / BEST_AGENT_MODE=off→'both'"""
    if BEST_AGENT_MODE != "on":
        return "both"
    if ACTIVE_AGENT_FILE.exists():
        try:
            v = ACTIVE_AGENT_FILE.read_text(encoding="utf-8").strip().lower()
            if v in AGENTS: return v
        except: pass
    return DEFAULT_AGENT if DEFAULT_AGENT in AGENTS else AGENTS[0]

def should_notify(agent):
    a = current_active_agent()
    return (a == "both") or (agent == a)

# ====== Discord ======
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

# ====== ロギング ======
def log_signal_row(agent, d):
    row = {
        "ts": jst_now_iso(), "agent": agent,
        "symbol": d.get("symbol"), "side": d.get("side"),
        "o": d.get("o"), "h": d.get("h"), "l": d.get("l"), "c": d.get("c"),
        "v": d.get("v"), "vwap": d.get("vwap"), "atr": d.get("atr"),
        "tf": d.get("tf"), "raw": json.dumps(d, ensure_ascii=False)
    }
    with csv_lock:
        with CSV_SIGNALS.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=row.keys()).writerow(row)

def log_trade_row(open_ts, close_ts, agent, symbol, pos_id, side, entry, exitp, pnl_pct, tf):
    with csv_lock:
        with CSV_TRADES.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=[
                "open_ts","close_ts","agent","symbol","pos_id","side","entry","exit","pnl_pct","tf"
            ]).writerow({
                "open_ts": open_ts, "close_ts": close_ts, "agent": agent,
                "symbol": symbol, "pos_id": pos_id, "side": side,
                "entry": entry, "exit": exitp, "pnl_pct": pnl_pct, "tf": tf
            })

# ====== Flask ======
app = Flask(__name__)

@app.get("/")
def root():
    return "ok"

@app.get("/signals")
def get_signals():
    return Response(CSV_SIGNALS.read_text("utf-8"), mimetype="text/csv")

@app.get("/trades")
def get_trades():
    return Response(CSV_TRADES.read_text("utf-8"), mimetype="text/csv")

def handle_event_for_agent(agent, data):
    symbol = (data.get("symbol") or data.get("ticker") or "UNKNOWN").upper()
    side   = (data.get("side") or "").lower()
    tf     = data.get("tf") or data.get("timeframe") or "-"
    price  = to_f(data.get("c") or data.get("close"))

    # 受信ログは常に残す（通知ミュートでも記録はする）
    log_signal_row(agent, data)

    # 主要イベントはデバウンス
    if side in ("buy","sell","tp","sl") and not pass_debounce(agent, symbol, side):
        return

    # エントリー
    if side in ("buy","sell") and price is not None:
        pos_id = f"{agent}-{symbol}-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        ACTIVE_POS.setdefault(agent, {})
        ACTIVE_POS[agent][symbol] = {
            "id": pos_id, "side": side, "entry": price, "tf": tf, "open_ts": jst_now_iso()
        }
        title = ("🟢[固定] 買い開始" if agent=="fixed" else "🟢[RT] 買い開始") if side=="buy" \
                else ("🔴[固定] 売り開始" if agent=="fixed" else "🔴[RT] 売り開始")
        desc  = f"銘柄: **{symbol}**\n時間足: {tf}\nエントリー価格: **{price}**\nポジションID: `{pos_id}`"
        if should_notify(agent):
            post_discord(title, desc, 0x2ecc71 if side=="buy" else 0xe74c3c)
        return

    # 決済
    if side in ("tp","sl") and price is not None:
        pos = ACTIVE_POS.get(agent, {}).get(symbol)
        pos_id = pos["id"] if pos else "N/A"
        entry  = pos["entry"] if pos else None
        opened = pos["open_ts"] if pos else "-"
        pnl_pct = None
        if entry is not None and pos:
            pnl_pct = (price/entry - 1.0) * 100.0 if pos["side"]=="buy" else (entry/price - 1.0) * 100.0
        pnl_r = None if pnl_pct is None else round(pnl_pct, 3)
        log_trade_row(opened, jst_now_iso(), agent, symbol, pos_id, pos["side"] if pos else "-", entry, price, pnl_r, tf)

        if should_notify(agent):
            if side == "tp":
                title = "🎯[固定] 利確" if agent=="fixed" else "🎯[RT] 利確"
                post_discord(title, f"銘柄: **{symbol}**\n時間足: {tf}\n約定価格: **{price}**\nエントリー: {entry}\nPnL: **{pnl_r}%**\nポジションID: `{pos_id}`", 0x3498db)
            else:
                title = "⚡[固定] 損切り" if agent=="fixed" else "⚡[RT] 損切り"
                post_discord(title, f"銘柄: **{symbol}**\n時間足: {tf}\n約定価格: **{price}**\nエントリー: {entry}\nPnL: **{pnl_r}%**\nポジションID: `{pos_id}`", 0xffc107)

        if agent in ACTIVE_POS and symbol in ACTIVE_POS[agent]:
            ACTIVE_POS[agent].pop(symbol, None)
        return

    # その他（任意通知）
    if side and should_notify(agent):
        ttl = "📈[固定] シグナル" if agent=="fixed" else "📈[RT] シグナル"
        post_discord(ttl, f"銘柄: **{symbol}** / 種別: {side} / 価格: {price}\nTF: {tf}", 0x95a5a6)

@app.route("/webhook", methods=["GET","POST"])
def webhook():
    # GET ?ping=1 は無通知（レスだけ）
    if request.method == "GET" and request.args.get("ping"):
        return jsonify({"ok": True, "ping": True})

    data = request.get_json(silent=True) or {}

    # ?agent=fixed/rt 指定が来たらその片方のみ処理。無ければ AGENTS 全部。
    agent_q = (request.args.get("agent") or "").lower().strip()
    agents = [agent_q] if agent_q in AGENTS else AGENTS

    for a in agents:
        handle_event_for_agent(a, data)

    return jsonify({"ok": True, "agents": agents})

if __name__ == "__main__":
    port = int(os.getenv("PORT","10000"))
    app.run(host="0.0.0.0", port=port)
