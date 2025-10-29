# position_manager.py
#
# ポジション(リアル採用と保留シャドウ)状態を読み書きするユーティリティ
# 保存先: data/positions_live.json

import os
import json
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

STATE_PATH = "data/positions_live.json"

# 却下してもこの分だけは「スカウト中」にして監視する
PENDING_OBSERVE_MINUTES = 3

# 同じ銘柄を同時に2本持たない（1銘柄1ポジ想定）
MAX_ONE_POSITION_PER_SYMBOL = True


def _now_jst():
    return datetime.now(JST)

def _iso_jst(dt=None):
    if dt is None:
        dt = _now_jst()
    return dt.isoformat(timespec="seconds")

def _load_all():
    if not os.path.exists(STATE_PATH):
        return {}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}

def _save_all(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def start_position(symbol, side, entry_price, accepted_real: bool):
    """
    ENTRY_BUY/ENTRY_SELL 受信時に呼ぶ。
    accepted_real=True なら status="real"（即Discordに出す対象）
    Falseなら status="shadow_pending"（保留で数分スカウト）
    """
    state = _load_all()

    # 既に未クローズのポジがあれば新規は無視（同銘柄2重持ちしない運用）
    if MAX_ONE_POSITION_PER_SYMBOL and symbol in state:
        sympos = state[symbol]
        if sympos.get("closed") is False:
            return state[symbol]

    pos = {
        "symbol": symbol,
        "side": side,  # "BUY" or "SELL"
        "entry_price": entry_price,
        "entry_time": _iso_jst(),
        "status": "real" if accepted_real else "shadow_pending",
        # statusは "real" / "shadow_pending" / "shadow_closed"
        "closed": False,
        "close_time": None,
        "close_price": None,
        "close_reason": None,

        # PRICE_TICKの履歴
        "ticks": [],

        # 却下スタート時刻（shadow_pendingの観察開始）
        "pending_start": _iso_jst(),
    }

    state[symbol] = pos
    _save_all(state)
    return pos

def add_tick(symbol, tick_data: dict):
    """
    PRICE_TICKで毎分呼ばれる。
    tick_dataの例:
    {
      "t": "2025-10-29T09:41:00+09:00",
      "price": float,
      "pct": float,
      "mins_from_entry": float|None,
      "vwap": float,
      "atr": float,
      "volume": float
    }
    """
    state = _load_all()
    if symbol not in state:
        return None

    pos = state[symbol]
    if pos.get("closed"):
        # すでにクローズ済みなら追記だけしない
        return pos

    pos["ticks"].append(tick_data)
    state[symbol] = pos
    _save_all(state)
    return pos

def promote_to_real(symbol):
    """
    shadow_pending → real に格上げ。
    これでDiscordに“後追いエントリー”を出せる状態になる。
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

def maybe_expire_shadow(symbol):
    """
    shadow_pendingを一定時間見たけど
    昇格させなかった場合は自然終了 (=学習用の"見送りでした"として閉じる)。
    """
    state = _load_all()
    if symbol not in state:
        return None, False

    pos = state[symbol]

    if pos.get("closed"):
        return pos, False

    if pos.get("status") != "shadow_pending":
        return pos, False

    try:
        start_dt = datetime.fromisoformat(pos["pending_start"])
    except Exception:
        start_dt = _now_jst()

    if _now_jst() - start_dt >= timedelta(minutes=PENDING_OBSERVE_MINUTES):
        # 期限切れでクローズ
        pos["status"] = "shadow_closed"
        pos["closed"] = True
        pos["close_time"] = _iso_jst()
        pos["close_reason"] = "expired_pending"
        state[symbol] = pos
        _save_all(state)
        return pos, True

    return pos, False

def force_close(symbol, reason, price_now=None):
    """
    AIの判断 or PineからのTP/SL/TIMEOUTでポジ終了させたい時に呼ぶ。
    """
    state = _load_all()
    if symbol not in state:
        return None

    pos = state[symbol]

    if pos.get("closed"):
        # もう終わってるなら何もしない
        return pos

    pos["closed"] = True
    # shadow_pending/shadow_closed系は"shadow_closed"として止める
    if pos["status"].startswith("shadow"):
        pos["status"] = "shadow_closed"
    pos["close_time"] = _iso_jst()
    pos["close_price"] = price_now
    pos["close_reason"] = reason

    state[symbol] = pos
    _save_all(state)
    return pos

def get_position(symbol):
    state = _load_all()
    return state.get(symbol)

def get_all_positions():
    return _load_all()
