import os
import json
import csv
from datetime import datetime
from flask import Flask, request, jsonify

from utils.discord import send_discord
from utils.time_utils import is_market_closed_now_jst, get_jst_now_str
from orchestrator import should_accept_signal, mark_symbol_active, mark_symbol_closed

# ------------------------------------------------------------
# パス / 環境変数
# ------------------------------------------------------------
DATA_DIR = "data"
STATE_PATH = os.path.join(DATA_DIR, "positions_state.json")
TRADE_LOG = os.path.join(DATA_DIR, "trade_log.csv")
REJECT_LOG = os.path.join(DATA_DIR, "rejected_signals.csv")
SYMBOL_NAME_PATH = os.path.join(DATA_DIR, "symbol_names.json")

DISCORD_MAIN = os.getenv("DISCORD_WEBHOOK_MAIN", "")
TV_SECRET = os.getenv("TV_SHARED_SECRET", "")
MARKET_CLOSE_HHMM = os.getenv("MARKET_CLOSE_HHMM", "15:25")

app = Flask(__name__)

# ------------------------------------------------------------
# ユーティリティ: ファイル/状態
# ------------------------------------------------------------
def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def load_json_safe(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return default

def load_state():
    return load_json_safe(STATE_PATH, {})

def save_state(state: dict):
    ensure_data_dir()
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def append_csv(path, row_dict, fieldnames):
    ensure_data_dir()
    file_exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row_dict)

# ------------------------------------------------------------
# 日本語銘柄名マップ
# ------------------------------------------------------------
def load_symbol_names():
    return load_json_safe(SYMBOL_NAME_PATH, {})

symbol_name_map = load_symbol_names()

def pretty_symbol(symbol: str) -> str:
    """
    "7203.T" -> "トヨタ自動車（7203.T）"
    マップに無いならそのまま "7203.T"
    """
    jp = symbol_name_map.get(symbol)
    if jp:
        return f"{jp}（{symbol}）"
    return symbol

# ------------------------------------------------------------
# ポジション管理
# ------------------------------------------------------------
def is_in_position(state: dict, symbol: str) -> bool:
    return symbol in state and state[symbol].get("open", False)

def open_position(state: dict, symbol: str, side: str, entry_price: float):
    state[symbol] = {
        "open": True,
        "side": side,  # "BUY" or "SELL"
        "entry_price": float(entry_price),
        "entry_time": get_jst_now_str()
    }

def close_position(state: dict, symbol: str, exit_price: float, reason: str):
    """
    reason: "TP", "SL", "TIMEOUT", "EOD"
    """
    if symbol not in state or not state[symbol].get("open", False):
        return None

    side = state[symbol]["side"]
    entry_price = float(state[symbol]["entry_price"])

    # PnLはシンプルにエントリーとの差（BUYなら上がれば+, SELLなら下がれば+）
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

    # state更新
    state[symbol]["open"] = False
    state[symbol]["exit_price"] = exit_price
    state[symbol]["close_time"] = get_jst_now_str()
    state[symbol]["close_reason"] = reason
    save_state(state)

    # orchestrator側にも「閉じたよ」と伝える
    mark_symbol_closed(symbol)

    return trade_row

def pct_change(entry_price: float, now_price: float, side: str) -> float:
    """
    エントリーからの%変化
    SELLは逆向きに符号反転して「自分に有利ならプラス」にそろえる
    """
    entry_price = float(entry_price)
    now_price = float(now_price)
    raw_pct = (now_price - entry_price) / entry_price * 100.0
    if side == "SELL":
        raw_pct = -raw_pct
    return raw_pct

# ------------------------------------------------------------
# Discordメッセージ生成
# ------------------------------------------------------------
def msg_entry(symbol: str, side: str, entry_price: float, tp_target, sl_target) -> str:
    """
    エントリー通知用
    tp_target / sl_target は orchestrator からもらった価格。Noneなら非表示。
    """
    sym_txt = pretty_symbol(symbol)
    icon = "🟢" if side == "BUY" else "🔴"
    side_jp = "買いエントリー" if side == "BUY" else "売りエントリー"

    lines = [
        f"{icon} {sym_txt} {side_jp}",
        f"IN価格: {entry_price}"
    ]

    if tp_target is not None:
        lines.append(f"利確目安: {tp_target}")
    if sl_target is not None:
        lines.append(f"損切り目安: {sl_target}")

    return "\n".join(lines)

def msg_close(symbol: str, side: str, now_price: float, pct_val: float, reason: str) -> str:
    """
    クローズ通知用
    """
    sym_txt = pretty_symbol(symbol)

    icons = {
        "TP": "🎯",        # 利確
        "SL": "⚡",        # 損切り
        "TIMEOUT": "⏱",   # タイムアウト終了
        "EOD": "🔔"       # 引け強制クローズ
    }
    labels = {
        "TP": "利確",
        "SL": "損切り",
        "TIMEOUT": "タイムアウト終了",
        "EOD": "引けクローズ(15:25)"
    }

    icon = icons.get(reason, "🔔")
    label = labels.get(reason, reason)
    side_jp = "買い" if side == "BUY" else "売り"

    return (
        f"{icon} {sym_txt} {label}\n"
        f"方向: {side_jp}\n"
        f"決済価格: {now_price}\n"
        f"変化率: {pct_val:.2f}%"
    )

# ------------------------------------------------------------
# GET /webhook (疎通確認用)
# ------------------------------------------------------------
@app.route("/webhook", methods=["GET"])
def webhook_ping():
    return jsonify({
        "status": "ready",
        "message": "POST /webhook でTradingViewシグナル受付中",
        "time": get_jst_now_str()
    }), 200

# ------------------------------------------------------------
# POST /webhook (本番)
# ------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook_post():
    """
    TradingView からの想定payload:
    {
      "secret": "...",
      "type": "ENTRY_BUY" | "ENTRY_SELL" |
               "PRICE_TICK" | "STEP_UP" | "STEP_DOWN" |
               "TP" | "SL" | "TIMEOUT",
      "symbol": "7203.T",
      "price": 1234.5,
      "step_label": "+1.0%"
    }
    """
    payload = request.get_json(silent=True) or {}

    # セキュリティ
    if payload.get("secret") != TV_SECRET:
        return jsonify({"status": "forbidden"}), 403

    signal_type = payload.get("type", "")
    symbol = payload.get("symbol", "")
    now_price = float(payload.get("price", 0))
    # step_label = payload.get("step_label", "")  # 今はDiscord投げないので未使用

    state = load_state()

    # まず「市場クローズ後 (=15:25以降)」は強制EODで閉じる
    if is_market_closed_now_jst(MARKET_CLOSE_HHMM):
        if is_in_position(state, symbol):
            side = state[symbol]["side"]
            entry_price = float(state[symbol]["entry_price"])
            pctv = pct_change(entry_price, now_price, side)

            close_position(state, symbol, now_price, "EOD")
            if DISCORD_MAIN:
                send_discord(DISCORD_MAIN, msg_close(symbol, side, now_price, pctv, "EOD"))

        return jsonify({"status": "after_close"}), 200

    # -------------------------
    # ENTRY: 新規エントリー
    # -------------------------
    if signal_type in ["ENTRY_BUY", "ENTRY_SELL"]:
        side = "BUY" if signal_type == "ENTRY_BUY" else "SELL"

        # AI判定:
        # accept: bool
        # reject_reason: str
        # tp_target: float or None
        # sl_target: float or None
        accept, reject_reason, tp_target, sl_target = should_accept_signal(symbol, side)

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

        # 同一銘柄2本目禁止
        if is_in_position(state, symbol):
            return jsonify({"status": "already_in_position"}), 200

        # ポジション開始
        open_position(state, symbol, side, now_price)
        save_state(state)
        mark_symbol_active(symbol)

        if DISCORD_MAIN:
            send_discord(
                DISCORD_MAIN,
                msg_entry(symbol, side, now_price, tp_target, sl_target)
            )

        return jsonify({"status": "entry_ok"}), 200

    # -------------------------
    # 経過系: PRICE_TICK / STEP_UP / STEP_DOWN
    # -------------------------
    if signal_type in ["PRICE_TICK", "STEP_UP", "STEP_DOWN"]:
        # 経過レポートはDiscordに送らないようにしてる
        return jsonify({"status": "progress_ok"}), 200

    # -------------------------
    # クローズ: TP / SL / TIMEOUT
    # -------------------------
    if signal_type in ["TP", "SL", "TIMEOUT"]:
        if is_in_position(state, symbol):
            side = state[symbol]["side"]
            entry_price = float(state[symbol]["entry_price"])
            pctv = pct_change(entry_price, now_price, side)

            close_position(state, symbol, now_price, signal_type)

            if DISCORD_MAIN:
                send_discord(
                    DISCORD_MAIN,
                    msg_close(symbol, side, now_price, pctv, signal_type)
                )

        return jsonify({"status": "close_ok"}), 200

    # それ以外
    return jsonify({"status": "ignored"}), 200

# ------------------------------------------------------------
# ルート "/" ヘルスチェック
# ------------------------------------------------------------
@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "time": get_jst_now_str()
    }), 200

# ------------------------------------------------------------
# メイン起動
# ------------------------------------------------------------
if __name__ == "__main__":
    ensure_data_dir()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
