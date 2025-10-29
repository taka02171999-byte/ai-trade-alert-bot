# position_manager.py
# ===============================
# ポジション状態・tick履歴を data/positions_live.json に保存。
# shadow_pendingも含めて全部ここで持つ。
# 学習ログ(data/learning_log.jsonl)にもクローズ確定時に1行追加。
# さらにshadow_pending→real昇格もここで扱う。
# ===============================

import os, json
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

STATE_PATH = "data/positions_live.json"
LEARN_PATH = "data/learning_log.jsonl"

def _now_iso():
    return datetime.now(JST).isoformat(timespec="seconds")

def _load_all():
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        try:
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
    ENTRY受信時。accepted_real=Trueならstatus="real"
    Falseならstatus="shadow_pending"
    """
    state = _load_all()

    state[symbol] = {
        "symbol": symbol,
        "side": side,     # "BUY" or "SELL"
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
    PRICE_TICKごとに呼ばれる。
    """
    state = _load_all()
    if symbol not in state:
        return None

    pos = state[symbol]
    if pos.get("closed"):
        return pos  # もう閉じてる

    pos["ticks"].append(tick_data)
    state[symbol] = pos
    _save_all(state)
    return pos

def promote_to_real(symbol):
    """
    shadow_pending → real に昇格させる。
    すでにclosedなら何もしない。
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
    AI利確/損切/タイムアウト or PineのTP/SL/TIMEOUTが来たら呼ぶ。
    クローズして、learning_log.jsonlにも結果をappendする。
    """
    state = _load_all()
    if symbol not in state:
        return None

    pos = state[symbol]

    # すでに閉じてるなら2重クローズさせない
    if pos.get("closed"):
        return pos

    pos["closed"] = True
    pos["close_time"] = _now_iso()
    pos["close_reason"] = reason
    pos["close_price"] = price_now

    if pct_now is None:
        if pos["ticks"]:
            pct_now = pos["ticks"][-1].get("pct")
        else:
            pct_now = None

    state[symbol] = pos
    _save_all(state)

    learn_row = {
        "symbol": symbol,
        "side": pos.get("side"),
        "status": pos.get("status"),  # real / shadow_pending (見送り) も記録される
        "entry_price": pos.get("entry_price"),
        "close_price": price_now,
        "final_pct": pct_now,
        "close_reason": reason,
        "entry_time": pos.get("entry_time"),
        "close_time": pos.get("close_time"),
        "ticks": pos.get("ticks", []),
    }
    _append_learning_log(learn_row)

    return pos

def get_position(symbol):
    state = _load_all()
    return state.get(symbol)
