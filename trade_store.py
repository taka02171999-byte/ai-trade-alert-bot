import os
import json
import csv
from typing import Optional, Dict, Any
from utils.time_utils import jst_now

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

STATE_PATH = os.path.join(DATA_DIR, "positions_state.json")
TRADES_PATH = os.path.join(DATA_DIR, "trades.csv")

TRADE_FIELDS = [
    "trade_id",
    "timestamp_entry",
    "timestamp_exit",
    "symbol",
    "name",
    "side",
    "session",
    "entry_price",
    "half_tp_price",
    "half_tp_pct",
    "exit_price",
    "exit_pct",
    "realized_pct",
    "exit_reason",
]

def _load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_state(state: Dict[str, Any]) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def _append_trade(row: Dict[str, Any]) -> None:
    file_exists = os.path.exists(TRADES_PATH)
    with open(TRADES_PATH, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
        if not file_exists:
            w.writeheader()
        # 欠けてても空で埋める
        safe = {k: row.get(k, "") for k in TRADE_FIELDS}
        w.writerow(safe)

def _make_trade_id(symbol: str) -> str:
    # 1銘柄1ポジ想定でOK（必要なら将来IDをユニーク化）
    return f"{symbol}_{jst_now().strftime('%Y%m%d_%H%M%S')}"

def get_open(symbol: str) -> Optional[Dict[str, Any]]:
    state = _load_state()
    pos = state.get(symbol)
    if not pos:
        return None
    if pos.get("closed"):
        return None
    return pos

def start_entry(symbol: str, name: str, side: str, session: str, price: float, pct_from_entry: Optional[float]) -> Dict[str, Any]:
    state = _load_state()

    trade_id = _make_trade_id(symbol)
    pos = {
        "trade_id": trade_id,
        "symbol": symbol,
        "name": name,
        "side": side,
        "session": session,
        "entry_price": float(price),
        "timestamp_entry": jst_now().isoformat(timespec="seconds"),

        "half_tp_price": None,
        "half_tp_pct": None,

        "closed": False,
    }

    state[symbol] = pos
    _save_state(state)

    return pos

def mark_half_tp(symbol: str, price: float, pct_from_entry: Optional[float]) -> Optional[Dict[str, Any]]:
    state = _load_state()
    pos = state.get(symbol)
    if not pos or pos.get("closed"):
        return None

    # 既に半利確済みなら上書きしない（2回目通知が来ても無視）
    if pos.get("half_tp_pct") is not None:
        return pos

    pos["half_tp_price"] = float(price)
    pos["half_tp_pct"] = float(pct_from_entry) if pct_from_entry is not None else None
    state[symbol] = pos
    _save_state(state)
    return pos

def close_position(symbol: str, exit_reason: str, price: float, pct_from_entry: Optional[float]) -> Optional[Dict[str, Any]]:
    state = _load_state()
    pos = state.get(symbol)
    if not pos or pos.get("closed"):
        return None

    exit_pct = float(pct_from_entry) if pct_from_entry is not None else None
    half_pct = pos.get("half_tp_pct")

    # 実現損益(%)：半分利確があれば 50% + 50% で合算
    # （サイズ比率は固定で 1/2 とする。将来変えるならここだけ）
    if half_pct is not None and exit_pct is not None:
        realized = 0.5 * float(half_pct) + 0.5 * float(exit_pct)
    elif exit_pct is not None:
        realized = float(exit_pct)
    else:
        realized = None

    closed_row = {
        "trade_id": pos.get("trade_id", ""),
        "timestamp_entry": pos.get("timestamp_entry", ""),
        "timestamp_exit": jst_now().isoformat(timespec="seconds"),
        "symbol": pos.get("symbol", ""),
        "name": pos.get("name", ""),
        "side": pos.get("side", ""),
        "session": pos.get("session", ""),
        "entry_price": pos.get("entry_price", ""),

        "half_tp_price": pos.get("half_tp_price", ""),
        "half_tp_pct": pos.get("half_tp_pct", ""),

        "exit_price": float(price),
        "exit_pct": exit_pct if exit_pct is not None else "",
        "realized_pct": round(realized, 4) if realized is not None else "",
        "exit_reason": exit_reason,
    }

    pos["closed"] = True
    state[symbol] = pos
    _save_state(state)

    _append_trade(closed_row)
    return closed_row
