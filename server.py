# server.py — Webhook 1本・fixed/rt 内部併走 + 勝者のみ通知 + 「厳選銘柄×採用AI」だけ通知 + 銘柄名表示（キャッシュ参照）
# 依存: flask, requests
import os, csv, json, uuid, requests, time, re
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify, Response
from threading import Lock

# ====== 設定（環境変数）======
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
AGENTS = [a.strip().lower() for a in os.getenv("AGENTS", "fixed,rt").split(",") if a.strip()]
if not AGENTS: AGENTS = ["fixed"]
DEBOUNCE_SEC = int(os.getenv("DEBOUNCE_SEC", "8"))           # 同一通知の連打抑制
MIN_HOLD_SEC = int(os.getenv("MIN_HOLD_SEC", "10"))           # 直後ヒゲ除外
MIN_ABS_PNL_PCT = float(os.getenv("MIN_ABS_PNL_PCT", "0.02")) # ±0%ノイズ除外(%)
BEST_AGENT_MODE = os.getenv("BEST_AGENT_MODE", "on").lower()  # "on": 勝者のみ通知
DEFAULT_AGENT = os.getenv("DEFAULT_AGENT", "fixed")

# ====== 永続ファイル ======
LOG_DIR = Path("logs"); LOG_DIR.mkdir(parents=True, exist_ok=True)
CSV_SIGNALS = LOG_DIR / "signals.csv"
CSV_TRADES  = LOG_DIR / "trades.csv"
ACTIVE_AGENT_FILE = Path("active_agent.txt")      # 比較レポート等が更新
NAME_CACHE = Path("symbol_names.json")            # 銘柄名キャッシュ(JSON)
SELECTED_JSON = LOG_DIR / "selected_symbols.json" # 夜のレポが更新（翌日の厳選）
OVERRIDES_JSON = Path("overrides_selected.json")  # 任意: 手動上書き

# CSV初期化
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
LAST_SENT = {}
ACTIVE_POS = {}

# ====== 共通関数 ======
JST = timezone(timedelta(hours=9))
def jst_now_iso(): return datetime.now(timezone.utc).astimezone(JST).isoformat()
def to_f(x, default=None):
    try: return float(x)
    except: return default
def pass_debounce(agent, symbol, side):
    key = (agent, symbol, side)
    now = time.time()
    last = LAST_SENT.get(key, 0)
    if now - last < DEBOUNCE_SEC: return False
    LAST_SENT[key] = now
    return True

def current_active_agent():
    if BEST_AGENT_MODE != "on": return "both"
    if ACTIVE_AGENT_FILE.exists():
        try:
            v = ACTIVE_AGENT_FILE.read_text(encoding="utf-8").strip().lower()
            if v in AGENTS: return v
        except: pass
    return DEFAULT_AGENT if DEFAULT_AGENT in AGENTS else AGENTS[0]

# --- 厳選銘柄 × 採用AI マップ ---
def load_selected_map():
    sel = {}
    if SELECTED_JSON.exists():
        try:
            raw = json.loads(SELECTED_JSON.read_text(encoding="utf-8"))
            sel = {str(k).upper(): str(v).lower() for k,v in raw.items() if str(v).lower() in ("fixed","rt")}
        except: sel = {}
    # 手動上書きは最優先
    if OVERRIDES_JSON.exists():
        try:
            ov = json.loads(OVERRIDES_JSON.read_text(encoding="utf-8"))
            for k,v in ov.items():
                if str(v).lower() in ("fixed","rt"):
                    sel[str(k).upper()] = str(v).lower()
        except: pass
    return sel

# --- 銘柄名キャッシュ参照 ---
def get_symbol_name(sym: str):
    try:
        if not NAME_CACHE.exists(): return None
        cache = json.loads(NAME_CACHE.read_text(encoding="utf-8"))
        name = cache.get(sym)
        if name: return re.sub(r"\s+", " ", str(name)).strip()
        return None
    except: return None
def label_with_name(sym: str):
    name = get_symbol_name(sym)
    return f"{sym} ({name})" if name else sym

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
        "symbol": (d.get("symbol") or d.get("ticker") or "UNKNOWN").upper(),
        "side": (d.get("side") or "").lower(),
        "o": d.get("o"), "h": d.get("h"), "l": d.get("l"), "c": d.get("c"),
        "v": d.get("v"), "vwap": d.get("vwap"), "atr": d.get("atr"),
        "tf": d.get("tf") or d.get("timeframe") or "-", "raw": json.dumps(d, ensure_ascii=False)
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
def root(): return "ok"

@app.get("/signals")
def get_signals(): return Response(CSV_SIGNALS.read_text("utf-8"), mimetype="text/csv")

@app.get("/trades")
def get_trades(): return Response(CSV_TRADES.read_text("utf-8"), mimetype="text/csv")

# ====== イベント処理 ======
def handle_event_for_agent(agent, data):
    symbol = (data.get("symbol") or data.get("ticker") or "UNKNOWN").upper()
    side   = (data.get("side") or "").lower()
    tf     = data.get("tf") or data.get("timeframe") or "-"
    price  = to_f(data.get("c") or data.get("close"))

    # 受信ログは常に残す
    log_signal_row(agent, data)

    # ★ 厳選フィルタ：マップがあれば「銘柄×採用AI」に一致するものだけ通す
    selected_map = load_selected_map()  # 空なら従来どおり全銘柄
    if selected_map:
        winner = current_active_agent()
        if winner == "both":
            if symbol not in selected_map:
                return
        else:
            if selected_map.get(symbol) != agent:
                return

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
        label = label_with_name(symbol)
        desc  = f"銘柄: **{label}**\n時間足: {tf}\nエントリー価格: **{price}**\nポジションID: `{pos_id}`"
        post_discord(title, desc, 0x2ecc71 if side=="buy" else 0xe74c3c)
        return

    # 決済（ヒゲ/同値ノイズは除外）
    if side in ("tp","sl") and price is not None:
        pos = ACTIVE_POS.get(agent, {}).get(symbol)
        pos_id = pos["id"] if pos else "N/A"
        entry  = pos["entry"] if pos else None
        opened = pos["open_ts"] if pos else "-"
        pnl_pct = None
        hold_ok = True
        if pos and entry is not None:
            pnl_pct = (price/entry - 1.0) * 100.0 if pos["side"]=="buy" else (entry/price - 1.0) * 100.0
            # 直後ヒゲ除外
            try:
                opened_dt = datetime.fromisoformat(opened)
                hold_sec = (datetime.now(timezone.utc).astimezone(JST) - opened_dt.astimezone(JST)).total_seconds()
                if hold_sec < MIN_HOLD_SEC:
                    hold_ok = False
            except: pass
        # ノイズ判定（記録・通知しない）
        if (pnl_pct is None) or (abs(pnl_pct) < MIN_ABS_PNL_PCT) or (not hold_ok):
            if agent in ACTIVE_POS and symbol in ACTIVE_POS[agent]:
                ACTIVE_POS[agent].pop(symbol, None)
            return

        pnl_r = round(pnl_pct, 3)
        # 記録
        log_trade_row(opened, jst_now_iso(), agent, symbol, pos_id, pos["side"] if pos else "-", entry, price, pnl_r, tf)

        # 通知
        label = label_with_name(symbol)
        if side == "tp":
            title = "🎯[固定] 利確" if agent=="fixed" else "🎯[RT] 利確"
            post_discord(title, f"銘柄: **{label}**\n時間足: {tf}\n約定価格: **{price}**\nエントリー: {entry}\nPnL: **{pnl_r}%**\nポジションID: `{pos_id}`", 0x3498db)
        else:
            title = "⚡[固定] 損切り" if agent=="fixed" else "⚡[RT] 損切り"
            post_discord(title, f"銘柄: **{label}**\n時間足: {tf}\n約定価格: **{price}**\nエントリー: {entry}\nPnL: **{pnl_r}%**\nポジションID: `{pos_id}`", 0xffc107)

        # 掃除
        if agent in ACTIVE_POS and symbol in ACTIVE_POS[agent]:
            ACTIVE_POS[agent].pop(symbol, None)
        return

    # 任意通知
    if side:
        ttl = "📈[固定] シグナル" if agent=="fixed" else "📈[RT] シグナル"
        label = label_with_name(symbol)
        post_discord(ttl, f"銘柄: **{label}** / 種別: {side} / 価格: {price}\nTF: {tf}", 0x95a5a6)

@app.route("/webhook", methods=["GET","POST"])
def webhook():
    if request.method == "GET" and request.args.get("ping"):
        return jsonify({"ok": True, "ping": True})
    data = request.get_json(silent=True) or {}
    agent_q = (request.args.get("agent") or "").lower().strip()
    agents = [agent_q] if agent_q in AGENTS else AGENTS
    for a in agents: handle_event_for_agent(a, data)
    return jsonify({"ok": True, "agents": agents})

if __name__ == "__main__":
    port = int(os.getenv("PORT","10000"))
    app.run(host="0.0.0.0", port=port)
