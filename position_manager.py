# position_manager.py
# ===============================
# ポジションの生存/保留/クローズ状態と、1分ごとのtick履歴を
# data/positions_live.json に保存する。
# shadow_pending もここで同じように扱う。
# ===============================

import os, json
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
STATE_PATH = "data/positions_live.json"
LEARN_PATH = "data/learning_log.jsonl"  # 学習用ログ

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
    ENTRY受信時に呼ぶ。
    accepted_real=True → status="real"（=本採用）
    accepted_real=False → status="shadow_pending"（=保留監視）
    """
    state = _load_all()

    state[symbol] = {
        "symbol": symbol,
        "side": side,                     # "BUY" or "SELL"
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
    PRICE_TICKごとに呼ぶ。
    tick_data例:
      {
        "t": <JST時刻ISO>,
        "price": float,
        "pct": float,
        "mins_from_entry": float,
        "volume": float,
        "vwap": float,
        "atr": float
      }
    """
    state = _load_all()
    if symbol not in state:
        return None

    pos = state[symbol]
    if pos.get("closed"):
        return pos

    pos["ticks"].append(tick_data)
    state[symbol] = pos
    _save_all(state)
    return pos

def force_close(symbol, reason, price_now, pct_now=None):
    """
    AI利確/損切 or PineのTP/SL/TIMEOUT保険でクローズするとき呼ぶ。
    ここで learning_log に1行吐く（学習用）。
    """
    state = _load_all()
    if symbol not in state:
        return None

    pos = state[symbol]

    # 既に閉じてたら2重で閉めない
    if pos.get("closed"):
        return pos

    pos["closed"] = True
    pos["close_time"] = _now_iso()
    pos["close_reason"] = reason
    pos["close_price"] = price_now

    # 最終損益（%）っぽいもの
    final_pct = pct_now
    if final_pct is None:
        # 最後のtickから拾う
        if pos["ticks"]:
            final_pct = pos["ticks"][-1].get("pct")
        else:
            final_pct = None

    state[symbol] = pos
    _save_all(state)

    # 学習ログに書き出し（shadow_pendingも含める）
    learn_row = {
        "symbol": symbol,
        "side": pos.get("side"),
        "entry_price": pos.get("entry_price"),
        "close_price": price_now,
        "close_reason": reason,
        "final_pct": final_pct,
        "ticks": pos.get("ticks", []),
        "status": pos.get("status"),
        "entry_time": pos.get("entry_time"),
        "close_time": pos.get("close_time"),
    }
    _append_learning_log(learn_row)

    return pos

def get_position(symbol):
    state = _load_all()
    return state.get(symbol)
