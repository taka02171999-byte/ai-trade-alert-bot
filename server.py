import os
import json
import csv
from datetime import datetime
from flask import Flask, request, jsonify
from utils.discord import send_discord
from utils.time_utils import is_market_closed_now_jst, get_jst_now_str
from orchestrator import should_accept_signal, mark_symbol_active, mark_symbol_closed

# ------------------------------------------------------------
# パスや環境変数
# ------------------------------------------------------------
DATA_DIR = "data"
STATE_PATH = os.path.join(DATA_DIR, "positions_state.json")
TRADE_LOG = os.path.join(DATA_DIR, "trade_log.csv")
REJECT_LOG = os.path.join(DATA_DIR, "rejected_signals.csv")

DISCORD_MAIN = os.getenv("DISCORD_WEBHOOK_MAIN", "")
TV_SECRET = os.getenv("TV_SHARED_SECRET", "")
MARKET_CLOSE_HHMM = os.getenv("MARKET_CLOSE_HHMM", "15:25")

app = Flask(__name__)

# ------------------------------------------------------------
# ファイルIO系
# ------------------------------------------------------------
def load_state():
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return {}

def save_state(state: dict):
    # data/ フォルダがなかったら作っておく保険
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def append_csv(path, row_dict, fieldnames):
    # data/ フォルダがなかったら作っておく保険
    os.makedirs(DATA_DIR, exist_ok=True)
    file_exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row_dict)

# ------------------------------------------------------------
# ポジション管理
# ------------------------------------------------------------
def is_in_position(state: dict, symbol: str) -> bool:
    return symbol in state and state[symbol].get("open", False)

def open_position(state: dict, symbol: str, side: str, entry_price: float):
    state[symbol] = {
        "open": True,
        "side": side,  # "BUY" or "SELL"
        "entry_price": entry_price,
        "entry_time": get_jst_now_str()
    }

def close_position(state: dict, symbol: str, exit_price: float, reason: str):
    """
    reason: "TP", "SL", "TIMEOUT", "EOD" など
    """
    if symbol not in state or not state[symbol].get("open", False):
        return None

    side = state[symbol]["side"]
    entry_price = float(state[symbol]["entry_price"])

    # pnl: BUYなら(OUT - IN)、SELLなら(IN - OUT)
    pnl_val = exit_price - entry_price if side == "BUY" else entry_price - exit_price

    trade_row = {
        "timestamp": get_jst_now_str(),
        "symbol": symbol,
        "side": side,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "reason": reason,
        "pnl": pnl_val
    }

    append_csv(
        TRADE_LOG,
        trade_row,
        ["timestamp", "symbol", "side", "entry_price", "exit_price", "reason", "pnl"]
    )

    # stateをクローズ状態に
    state[symbol]["open"] = False
    state[symbol]["exit_price"] = exit_price
    state[symbol]["close_time"] = get_jst_now_str()
    state[symbol]["close_reason"] = reason
    save_state(state)

    # orchestrator側にも「閉じたよ」を伝える
    mark_symbol_closed(symbol)

    return trade_row

def pct(entry_price: float, now_price: float, side: str) -> float:
    """
    エントリーからの変化率[%]
    side="SELL" のときは逆方向で評価
    """
    diff_pct = (now_price - entry_price) / entry_price * 100.0
    return -diff_pct if side == "SELL" else diff_pct

# ------------------------------------------------------------
# Discordメッセージ生成
# ------------------------------------------------------------
def msg_entry(symbol: str, side: str, price: float) -> str:
    if side == "BUY":
        icon = "🟢"
        jp_side = "買いエントリー"
    else:
        icon = "🔴"
        jp_side = "売りエントリー"
    return (
        f"{icon} {symbol} {jp_side}\n"
        f"IN価格: {price}"
    )

def msg_progress(symbol: str, side: str, entry_price: float, now_price: float, pct_val: float, step_label: str) -> str:
    direction = "買い中" if side == "BUY" else "売り中"
    return (
        f"📈 {symbol} 経過 ({direction})\n"
        f"現在値: {now_price} / IN: {entry_price}\n"
        f"変化: {pct_val:.2f}% ({step_label})"
    )

def msg_close(symbol: str, side: str, now_price: float, pct_val: float, reason: str) -> str:
    icons = {
        "TP": "🟩",
        "SL": "🟥",
        "TIMEOUT": "⏱",
        "EOD": "🔔"
    }
    labels = {
        "TP": "利確クローズ",
        "SL": "損切りクローズ",
        "TIMEOUT": "タイムアウト終了",
        "EOD": "引け強制クローズ(15:25)"
    }
    icon = icons.get(reason, "🔔")
    label = labels.get(reason, reason)
    dir_jp = "買い" if side == "BUY" else "売り"
    return (
        f"{icon} {symbol} {label}\n"
        f"決済価格: {now_price} / 方向: {dir_jp}\n"
        f"変化率: {pct_val:.2f}%"
    )

# ------------------------------------------------------------
# /webhook 健康チェック (GET用)
# これを入れることで UptimeRobot とかブラウザGETで404にならない
# ------------------------------------------------------------
@app.route("/webhook", methods=["GET"])
def webhook_ping():
    return jsonify({
        "status": "ready",
        "message": "POST /webhook でTradingViewシグナル受付中",
        "time": get_jst_now_str()
    }), 200

# ------------------------------------------------------------
# /webhook 本番 (POST: TradingViewが叩く)
# ------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView側からの想定payload:
    {
      "secret": "...",
      "type": "ENTRY_BUY" | "ENTRY_SELL" |
               "PRICE_TICK" |
               "STEP_UP" | "STEP_DOWN" |
               "TP" | "SL" | "TIMEOUT",
      "symbol": "7203.T",
      "price": 1234.5,
      "step_label": "+1.0%"  # STEP_UP/DOWN用
    }
    """
    payload = request.get_json(silent=True) or {}

    # セキュリティチェック
    if payload.get("secret") != TV_SECRET:
        return jsonify({"status": "forbidden"}), 403

    signal_type = payload.get("type", "")
    symbol = payload.get("symbol", "")
    now_price = float(payload.get("price", 0))
    step_label = payload.get("step_label", "")

    # 現在の全ポジ state
    state = load_state()

    # まず引け（15:25以降）の扱い。強制クローズ優先。
    if is_market_closed_now_jst(MARKET_CLOSE_HHMM):
        if is_in_position(state, symbol):
            side = state[symbol]["side"]
            entry_price = float(state[symbol]["entry_price"])
            pct_val = pct(entry_price, now_price, side)

            # "EOD"（引けクローズ）として記録
            close_position(state, symbol, now_price, "EOD")
            send_discord(DISCORD_MAIN, msg_close(symbol, side, now_price, pct_val, "EOD"))

        return jsonify({"status": "after_close"}), 200

    # --- エントリー (ENTRY_BUY / ENTRY_SELL) ---
    if signal_type in ["ENTRY_BUY", "ENTRY_SELL"]:
        side = "BUY" if signal_type == "ENTRY_BUY" else "SELL"

        # orchestratorで採用するか？ Top10か？ クールダウン中じゃないか？
        accept, reject_reason = should_accept_signal(symbol, side)
        if not accept:
            append_csv(
                REJECT_LOG,
                {
                    "timestamp": get_jst_now_str(),
                    "symbol": symbol,
                    "side": side,
                    "reason": reject_reason
                },
                ["timestamp", "symbol", "side", "reason"]
            )
            return jsonify({"status": "rejected_by_ai"}), 200

        # 同じ銘柄で既にポジション中なら2本目は禁止
        if is_in_position(state, symbol):
            return jsonify({"status": "already_in_position"}), 200

        # ポジション開始
        open_position(state, symbol, side, now_price)
        save_state(state)
        mark_symbol_active(symbol)

        send_discord(DISCORD_MAIN, msg_entry(symbol, side, now_price))
        return jsonify({"status": "entry_ok"}), 200

    # --- 経過通知 (PRICE_TICK / STEP_UP / STEP_DOWN) ---
    if signal_type in ["PRICE_TICK", "STEP_UP", "STEP_DOWN"]:
        if is_in_position(state, symbol):
            side = state[symbol]["side"]
            entry_price = float(state[symbol]["entry_price"])
            pct_val = pct(entry_price, now_price, side)
            label = "PRICE_TICK" if signal_type == "PRICE_TICK" else step_label

            send_discord(
                DISCORD_MAIN,
                msg_progress(symbol, side, entry_price, now_price, pct_val, label)
            )
        return jsonify({"status": "progress_ok"}), 200

    # --- クローズ (TP / SL / TIMEOUT) ---
    if signal_type in ["TP", "SL", "TIMEOUT"]:
        if is_in_position(state, symbol):
            side = state[symbol]["side"]
            entry_price = float(state[symbol]["entry_price"])
            pct_val = pct(entry_price, now_price, side)

            close_position(state, symbol, now_price, signal_type)
            send_discord(DISCORD_MAIN, msg_close(symbol, side, now_price, pct_val, signal_type))

        return jsonify({"status": "close_ok"}), 200

    # --- それ以外のtypeは無視扱い ---
    return jsonify({"status": "ignored"}), 200

# ------------------------------------------------------------
# / 健康チェック (GET)
# ------------------------------------------------------------
@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "time": get_jst_now_str()
    }), 200

# ------------------------------------------------------------
# メイン起動 (RenderのStart Commandで使う)
# ------------------------------------------------------------
if __name__ == "__main__":
    # 念のためディレクトリだけ先に作っておく
    os.makedirs(DATA_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
