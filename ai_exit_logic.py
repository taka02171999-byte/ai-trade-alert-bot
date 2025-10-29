# ai_exit_logic.py (AI完全自律対応版)
import os, json

MODEL_PATH = "data/ai_dynamic_thresholds.json"

def _load_model():
    if not os.path.exists(MODEL_PATH): return {}
    with open(MODEL_PATH, encoding="utf-8") as f: return json.load(f)

MODEL = _load_model()

def should_exit_now(position_dict):
    """AIがリアルタイムで利確/損切り判断する"""
    if not position_dict or position_dict.get("closed"): return False, None
    ticks = position_dict.get("ticks", [])
    if not ticks: return False, None

    last = ticks[-1]
    sym = position_dict["symbol"]
    side = position_dict["side"]
    pct = float(last.get("pct", 0))
    price = float(last.get("price", 0))
    mins = float(last.get("mins_from_entry", 0))
    vol = float(last.get("volume", 0) or 0)
    atr = float(last.get("atr", 0) or 0)
    vwap = float(last.get("vwap", 0) or 0)

    thresholds = MODEL.get(sym, {"tp": 1.0, "sl": -0.6})
    tp, sl = thresholds["tp"], thresholds["sl"]

    if vol > 0 and atr > 0:
        factor = min(vol / (atr * 10000), 2.0)
        tp *= factor
    if side == "BUY" and vwap and price < vwap:
        sl = max(sl, -0.4)
    if side == "SELL" and vwap and price > vwap:
        sl = max(sl, -0.4)

    if pct >= tp: return True, ("AI_TP", price)
    if pct <= sl: return True, ("AI_SL", price)
    if mins >= 15: return True, ("AI_TIMEOUT", price)
    return False, None
