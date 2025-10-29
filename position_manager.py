# position_manager.py
# ===============================
# ポジションとtick履歴をファイルで管理
# data/positions_live.json : 現在の保有/監視状況
# data/learning_log.jsonl  : 閉じたポジを学習ログとして追記
# ===============================

import os
import json
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

STATE_PATH = "data/positions_live.json"
LEARN_PATH = "data/learning_log.jsonl"


def _now_iso():
    return datetime.now(JST).isoformat(timespec="seconds")


def _load_all():
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def _save_all(state):
    os.makedirs("data", exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _append_learning_log(row: dict):
    os.makedirs("data", exist_ok=True)
    with open(LEARN_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def start_position(symbol, side, price, accepted_real):
    """
    ENTRY受信時に呼ぶ。
    accepted_real=True  → status="real"（正式エントリー）
    accepted_real=False → status="shadow_pending"（保留監視）
    """
    state = _load_all()

    state[symbol] = {
        "symbol": symbol,
        "side": side,            # "BUY" or "SELL"
        "entry_price": price,
        "entry_time": _now_iso(),
        "status": "real" if accepted_real else "shadow_pending",
        "closed": False,
        "close_time": None,
        "close_reason": None,
        "close_price": None,
        "ticks": []
    }

    _save_all(state)
    return state[symbol]


def add_tick(symbol, tick_data: dict):
    """
    PRICE_TICKで毎分呼ぶ。
    tick_dataには最低:
      {
        "t": <JST時刻ISO>,
        "price": float,
        "pct": float,
        "mins_from_entry": float or str,
        "volume": ...,
        "vwap": ...,
        "atr": ...
      }
    """
    state = _load_all()
    if symbol not in state:
        return None

    pos = state[symbol]
    if pos.get("closed"):
        return pos  # もう閉じてるならそのまま

    pos["ticks"].append(tick_data)
    state[symbol] = pos
    _save_all(state)
    return pos


def promote_to_real(symbol):
    """
    shadow_pending → real に格上げ。
    """
    state = _load_all()
    if symbol not in state:
        return None

    pos = state[symbol]
    if pos.get("closed"):
        return pos

    if pos.get("status") == "shadow_pending":
        pos["status"] = "real"

    state[symbol] = pos
    _save_all(state)
    return pos


def force_close(symbol, reason, price_now, pct_now=None):
    """
    AI側 or Pine側でクローズが決まったときに呼ぶ。
    - ポジをclosedにする
    - 学習ログ(learning_log.jsonl)に行を追加して将来の学習に使う
    """
    state = _load_all()
    if symbol not in state:
        return None

    pos = state[symbol]
    if pos.get("closed"):
        return pos  # もう閉じてるなら二重で閉じない

    pos["closed"] = True
    pos["close_time"] = _now_iso()
    pos["close_reason"] = reason
    pos["close_price"] = price_now

    # pct_now が来てなかったら ticks最後から推定
    if pct_now is None:
        if pos["ticks"]:
            pct_now = pos["ticks"][-1].get("pct")
        else:
            pct_now = None

    state[symbol] = pos
    _save_all(state)

    learn_row = {
        "symbol": pos.get("symbol"),
        "side": pos.get("side"),
        "status": pos.get("status"),  # real / shadow_pending (見送りパターンも残る)
        "entry_price": pos.get("entry_price"),
        "entry_time": pos.get("entry_time"),

        "close_price": price_now,
        "close_time": pos.get("close_time"),
        "close_reason": reason,

        "final_pct": pct_now,
        "ticks": pos.get("ticks", []),
    }
    _append_learning_log(learn_row)

    return pos


def get_position(symbol):
    state = _load_all()
    return state.get(symbol)
