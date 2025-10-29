# ai_exit_logic.py
# ===============================
# 保有ポジションの「AI利確/AI損切り/AIタイムアウト」判定
# PRICE_TICKごとに呼ばれる
#
# 返り値:
#   (False, None)
#   (True, ("AI_TP", price_now))
#   (True, ("AI_SL", price_now))
#   (True, ("AI_TIMEOUT", price_now))
# ===============================

import os
import json

MODEL_PATH = "data/ai_dynamic_thresholds.json"


def _load_model():
    """
    data/ai_dynamic_thresholds.json から銘柄別しきい値を読む
    {
      "7203.T": { "tp": 1.2, "sl": -0.7 },
      "6758.T": { "tp": 0.9, "sl": -0.5 }
    }
    無ければデフォルト tp=+1.0%, sl=-0.6%
    """
    if not os.path.exists(MODEL_PATH):
        return {}
    try:
        with open(MODEL_PATH, encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def should_exit_now(position_dict):
    if not position_dict or position_dict.get("closed"):
        return False, None

    ticks = position_dict.get("ticks", [])
    if not ticks:
        return False, None

    last = ticks[-1]

    sym       = position_dict.get("symbol")
    side      = position_dict.get("side")
    pct_now   = float(last.get("pct", 0) or 0)              # 含み率[%]（SELLでも有利方向なら+）
    price_now = float(last.get("price", 0) or 0)
    mins_open = float(last.get("mins_from_entry", 0) or 0)

    vol_now   = float(last.get("volume", 0) or 0)
    atr_now   = float(last.get("atr", 0) or 0)
    vwap_now  = float(last.get("vwap", 0) or 0)

    # 銘柄別しきい値をロード
    model = _load_model()
    sym_cfg = model.get(sym, {"tp": 1.0, "sl": -0.6})
    tp_thr = float(sym_cfg.get("tp", 1.0))
    sl_thr = float(sym_cfg.get("sl", -0.6))

    # 出来高×ATRが熱いなら、利確幅を引っ張る（tp_thrを引き上げ）
    if vol_now > 0 and atr_now > 0:
        heat = min(vol_now / (atr_now * 10000.0), 2.0)
        tp_thr *= heat

    # VWAPを明確に割った/上抜け戻したら「もう勢いない」ってことで損切りライン浅く
    if side == "BUY" and vwap_now and price_now < vwap_now:
        sl_thr = max(sl_thr, -0.4)
    if side == "SELL" and vwap_now and price_now > vwap_now:
        sl_thr = max(sl_thr, -0.4)

    # 利確判定
    if pct_now >= tp_thr:
        return True, ("AI_TP", price_now)

    # 損切り判定
    if pct_now <= sl_thr:
        return True, ("AI_SL", price_now)

    # ダラダラしてるなら強制撤退（AI側のタイムアウト）
    if mins_open >= 15:
        return True, ("AI_TIMEOUT", price_now)

    return False, None
