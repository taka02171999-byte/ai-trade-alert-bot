# server.py — Webhook 1本・fixed/rt 内部併走 + 勝者のみ通知（銘柄名表示/ウォッチフィルタ/孤立決済ガード/8秒デバウンス）
# 依存: flask, requests
import os, csv, json, uuid, requests, time, re, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, Response
from threading import Lock
from functools import lru_cache

# ====== 設定（環境変数）======
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
AGENTS = [a.strip().lower() for a in os.getenv("AGENTS", "fixed,rt").split(",") if a.strip()]
if not AGENTS: AGENTS = ["fixed"]
DEBOUNCE_SEC = int(os.getenv("DEBOUNCE_SEC", "8"))
BEST_AGENT_MODE = os.getenv("BEST_AGENT_MODE", "on").lower()   # "on" なら勝者のみ通知
DEFAULT_AGENT = os.getenv("DEFAULT_AGENT", "fixed")
EXECUTE_WINNER_ONLY = os.getenv("EXECUTE_WINNER_ONLY", "off").lower()  # 将来用：勝者だけ実行

# 通知をウォッチリスト銘柄に限定
FILTER_SYMBOLS = os.getenv("FILTER_SYMBOLS", "off").lower()    # "on" で有効
WATCHLIST_FILE = Path("watchlist.txt")

# 直後ヒゲ/ゼロPnL対策ガード（必要に応じて調整）
MIN_HOLD_SEC = int(os.getenv("MIN_HOLD_SEC", "10"))                 # エントリーから10秒未満のTP/SLは無視
MIN_ABS_PNL_PCT = float(os.getenv("MIN_ABS_PNL_PCT", "0.02"))       # ±0.02%未満はノイズとして無視

# ====== 永続ファイル ======
LOG_DIR = Path("logs"); LOG_DIR.mkdir(parents=True, exist_ok=True)
CSV_SIGNALS = LOG_DIR / "signals.csv"
CSV_TRADES  = LOG_DIR / "trades.csv"
ACTIVE_AGENT_FILE = Path("active_agent.txt")

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
LAST_SENT = {}   # {(agent,symbol,side): ts}
ACTIVE_POS = {}  # {agent: {symbol: {...}}}

# ====== JSTユーティリティ ======
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
    """勝者ファイル→その値 / 無い→DEFAULT_AGENT / BEST_AGENT_MODE=off→'both'"""
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

# ====== 銘柄名の自動取得＆キャッシュ ======
SYMBOL_CACHE_FILE = Path("symbol_names.json")

def _load_symbol_cache():
    if SYMBOL_CACHE_FILE.exists():
        try:
            return json.loads(SYMBOL_CACHE_FILE.read_text(encoding="utf-8"))
        except:
            return {}
    return {}

def _save_symbol_cache(cache: dict):
    try:
        SYMBOL_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[warn] save_symbol_cache: {e}")

SYMBOL_NAMES = _load_symbol_cache()

@lru_cache(maxsize=512)
def lookup_symbol_name(symbol: str):
    """初回だけYahoo!で名称取得→symbol_names.jsonに保存。2回目以降はキャッシュ即時参照。"""
    if not symbol or not symbol.isdigit():
        return None
    if symbol in SYMBOL_NAMES:
        return SYMBOL_NAMES[symbol]
    try:
        url = f"https://finance.yahoo.co.jp/quote/{symbol}.T"
        with urllib.request.urlopen(url, timeout=5) as res:
            html = res.read().decode("utf-8", errors="ignore")
        m = re.search(r"<title>([^（(]+)[（(]", html)
        if m:
            name = m.group(1).strip()
            SYMBOL_NAMES[symbol] = name
            _save_symbol_cache(SYMBOL_NAMES)
            return name
    except Exception as e:
        print(f"[warn] lookup_symbol_name({symbol}): {e}")
    return None

def symbol_display(symbol: str) -> str:
    name = lookup_symbol_name(symbol)
    return f"{symbol} ({name})" if name else symbol

def in_watchlist(symbol: str) -> bool:
    if FILTER_SYMBOLS != "on":
        return True
    try:
        if not WATCHLIST_FILE.exists():
            return True
        wl_raw = WATCHLIST_FILE.read_text(encoding="utf-8").strip()
        syms = {s.strip().upper() for s in wl_raw.split(",") if s.strip()}
        return (symbol.upper() in syms) if syms else True
    except:
        return True

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
    # 余計なサフィックスを落とす（3479.T → 3479）
    if symbol.endswith(".T"): symbol = symbol[:-2]
    symbol_disp = symbol_display(symbol)
    side   = (data.get("side") or "").lower()
    tf     = data.get("tf") or data.get("timeframe") or "-"
    price  = to_f(data.get("c") or data.get("close"))

    # 常にログは残す
    log_signal_row(agent, data)

    # 通知可否（勝者 & ウォッチリスト）
    notify_ok = should_notify(agent) and in_watchlist(symbol)

    # デバウンス
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
        desc  = f"銘柄: **{symbol_disp}**\n時間足: {tf}\nエントリー価格: **{price}**\nポジションID: `{pos_id}`"
        if notify_ok:
            post_discord(title, desc, 0x2ecc71 if side=="buy" else 0xe74c3c)
        return

    # 決済
    if side in ("tp","sl") and price is not None:
        pos = ACTIVE_POS.get(agent, {}).get(symbol)

        # ★エントリーが無い決済は無視（通知・記録どちらもスキップ）
        if not pos:
            print(f"[orphan-exit] ignore {side} for {agent}/{symbol} (no active position)")
            return

        pos_id = pos["id"]
        entry  = pos["entry"]
        opened = pos["open_ts"]
        pnl_pct = (price/entry - 1.0) * 100.0 if pos["side"]=="buy" else (entry/price - 1.0) * 100.0
        pnl_r = round(pnl_pct, 3)

        # --- 直後ヒゲ/ゼロPnL対策 ---
        # エントリー直後の決済は無視
        try:
            opened_dt = datetime.fromisoformat(opened)
            hold_sec = (datetime.now(timezone.utc) - opened_dt.astimezone(timezone.utc)).total_seconds()
        except Exception:
            hold_sec = None
        if hold_sec is not None and hold_sec < MIN_HOLD_SEC:
            print(f"[guard] ignore {side} for {symbol}: hold {hold_sec:.1f}s < {MIN_HOLD_SEC}s")
            return
        # PnLがほぼ0%ならノイズ扱い
        if abs(pnl_r) < MIN_ABS_PNL_PCT:
            print(f"[guard] ignore {side} for {symbol}: pnl {pnl_r}% < {MIN_ABS_PNL_PCT}%")
            return

        log_trade_row(opened, jst_now_iso(), agent, symbol, pos_id, pos["side"], entry, price, pnl_r, tf)

        if notify_ok:
            if side == "tp":
                title = "🎯[固定] 利確" if agent=="fixed" else "🎯[RT] 利確"
                post_discord(title, f"銘柄: **{symbol_disp}**\n時間足: {tf}\n約定価格: **{price}**\nエントリー: {entry}\nPnL: **{pnl_r}%**\nポジションID: `{pos_id}`", 0x3498db)
            else:
                title = "⚡[固定] 損切り" if agent=="fixed" else "⚡[RT] 損切り"
                post_discord(title, f"銘柄: **{symbol_disp}**\n時間足: {tf}\n約定価格: **{price}**\nエントリー: {entry}\nPnL: **{pnl_r}%**\nポジションID: `{pos_id}`", 0xffc107)

        # ポジション解消
        ACTIVE_POS[agent].pop(symbol, None)
        return

    # その他（任意通知）
    if side and notify_ok:
        ttl = "📈[固定] シグナル" if agent=="fixed" else "📈[RT] シグナル"
        post_discord(ttl, f"銘柄: **{symbol_disp}** / 種別: {side} / 価格: {price}\nTF: {tf}", 0x95a5a6)

@app.route("/webhook", methods=["GET","POST"])
def webhook():
    # GET ?ping=1 は無通知（レスだけ）
    if request.method == "GET" and request.args.get("ping"):
        return jsonify({"ok": True, "ping": True})

    data = request.get_json(silent=True) or {}

    # ?agent=fixed/rt が来たらその片方のみ（テスト/強制上書き）
    agent_q = (request.args.get("agent") or "").lower().strip()
    if agent_q in AGENTS:
        agents = [agent_q]
    else:
        # 実行範囲（通知とは独立）
        if EXECUTE_WINNER_ONLY == "on":
            act = current_active_agent()   # "fixed" / "rt" / "both"
            agents = AGENTS if act == "both" else [act]
        else:
            agents = AGENTS               # 両方実行

    for a in agents:
        handle_event_for_agent(a, data)

    return jsonify({"ok": True, "agents": agents})

if __name__ == "__main__":
    port = int(os.getenv("PORT","10000"))
    app.run(host="0.0.0.0", port=port)
