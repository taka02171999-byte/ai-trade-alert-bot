import os, json, csv
from datetime import datetime
from flask import Flask, request, jsonify
from utils.discord import send_discord
from utils.time_utils import is_market_closed_now_jst, get_jst_now_str
from orchestrator import should_accept_signal, mark_symbol_active, mark_symbol_closed

DATA_DIR = "data"
STATE_PATH = os.path.join(DATA_DIR, "positions_state.json")
TRADE_LOG = os.path.join(DATA_DIR, "trade_log.csv")
REJECT_LOG = os.path.join(DATA_DIR, "rejected_signals.csv")

DISCORD_MAIN = os.getenv("DISCORD_WEBHOOK_MAIN", "")
TV_SECRET = os.getenv("TV_SHARED_SECRET", "")
MARKET_CLOSE_HHMM = os.getenv("MARKET_CLOSE_HHMM", "15:25")

app = Flask(__name__)

# ------------------------------------------------------------
# ファイル操作
# ------------------------------------------------------------
def load_state():
    if not os.path.exists(STATE_PATH): return {}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        try: return json.load(f)
        except: return {}

def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def append_csv(path, row, fields):
    first = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if first: w.writeheader()
        w.writerow(row)

# ------------------------------------------------------------
# ポジション管理
# ------------------------------------------------------------
def is_in_position(state, symbol):
    return symbol in state and state[symbol].get("open", False)

def open_position(state, symbol, side, entry_price):
    state[symbol] = {
        "open": True,
        "side": side,
        "entry_price": entry_price,
        "entry_time": get_jst_now_str()
    }

def close_position(state, symbol, exit_price, reason):
    if symbol not in state or not state[symbol].get("open", False): return None
    side = state[symbol]["side"]
    entry_price = float(state[symbol]["entry_price"])
    pnl = exit_price - entry_price if side == "BUY" else entry_price - exit_price
    row = {
        "timestamp": get_jst_now_str(),
        "symbol": symbol,
        "side": side,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "reason": reason,
        "pnl": pnl
    }
    append_csv(TRADE_LOG, row, ["timestamp","symbol","side","entry_price","exit_price","reason","pnl"])
    state[symbol]["open"] = False
    mark_symbol_closed(symbol)
    save_state(state)
    return row

def pct(entry, now, side):
    diff = (now - entry)/entry*100
    return -diff if side == "SELL" else diff

# ------------------------------------------------------------
# Discord通知文
# ------------------------------------------------------------
def msg_entry(symbol, side, price):
    icon = "🟢" if side == "BUY" else "🔴"
    jp = "買い" if side == "BUY" else "売り"
    return f"{icon} {symbol} {jp}エントリー\n価格: {price}"

def msg_progress(symbol, side, entry, now, pct_val, step):
    dir = "買い中" if side == "BUY" else "売り中"
    return f"📈 {symbol} {dir}\n現値: {now} / IN: {entry}\n変化: {pct_val:.2f}% ({step})"

def msg_close(symbol, side, price, pct_val, reason):
    icons = {"TP":"🟩","SL":"🟥","TIMEOUT":"⏱","EOD":"🔔"}
    label = {"TP":"利確","SL":"損切","TIMEOUT":"時間切れ","EOD":"引け"}
    return f"{icons[reason]} {symbol} {label[reason]}\n価格: {price}\n変化率: {pct_val:.2f}%"

# ------------------------------------------------------------
# Webhook
# ------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    p = request.get_json(silent=True) or {}
    if p.get("secret") != TV_SECRET:
        return jsonify({"status":"forbidden"}),403

    t, s, price = p.get("type",""), p.get("symbol",""), float(p.get("price",0))
    step = p.get("step_label","")
    state = load_state()

    # 市場クローズ処理
    if is_market_closed_now_jst(MARKET_CLOSE_HHMM):
        if is_in_position(state,s):
            side = state[s]["side"]
            pctv = pct(state[s]["entry_price"],price,side)
            close_position(state,s,price,"EOD")
            send_discord(DISCORD_MAIN,msg_close(s,side,price,pctv,"EOD"))
        return jsonify({"status":"closed"}),200

    # ENTRY
    if t in ["ENTRY_BUY","ENTRY_SELL"]:
        side = "BUY" if t=="ENTRY_BUY" else "SELL"
        ok, why = should_accept_signal(s,side)
        if not ok:
            append_csv(REJECT_LOG,{"timestamp":get_jst_now_str(),"symbol":s,"side":side,"reason":why},["timestamp","symbol","side","reason"])
            return jsonify({"status":"rejected"}),200
        if is_in_position(state,s):
            return jsonify({"status":"already"}),200
        open_position(state,s,side,price)
        save_state(state)
        mark_symbol_active(s)
        send_discord(DISCORD_MAIN,msg_entry(s,side,price))
        return jsonify({"status":"entry_ok"}),200

    # 経過
    if t in ["PRICE_TICK","STEP_UP","STEP_DOWN"]:
        if is_in_position(state,s):
            side = state[s]["side"]
            entry = state[s]["entry_price"]
            pctv = pct(entry,price,side)
            label = "PRICE_TICK" if t=="PRICE_TICK" else step
            send_discord(DISCORD_MAIN,msg_progress(s,side,entry,price,pctv,label))
        return jsonify({"status":"progress"}),200

    # クローズ
    if t in ["TP","SL","TIMEOUT"]:
        if is_in_position(state,s):
            side = state[s]["side"]
            entry = state[s]["entry_price"]
            pctv = pct(entry,price,side)
            close_position(state,s,price,t)
            send_discord(DISCORD_MAIN,msg_close(s,side,price,pctv,t))
        return jsonify({"status":"close"}),200

    return jsonify({"status":"ignored"}),200

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status":"ok","time":get_jst_now_str()}),200

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
