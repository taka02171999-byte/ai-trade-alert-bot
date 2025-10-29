# ai_exit_logic.py
# ===============================
# ポジション保有中の「利確/損切/タイムアウト」判断
# PRICE_TICKごとに呼ばれる
# ===============================

import os, json

MODEL_PATH = "data/ai_dynamic_thresholds.json"

def _load_model():
    """
    銘柄ごとのTP/SLしきい値を毎回読み直す。
    学習ジョブ(ai_model_trainer.py)が走ったあとの最新値を使うため。
    """
    if not os.path.exists(MODEL_PATH):
        return {}
    with open(MODEL_PATH, encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return {}

def should_exit_now(position_dict):
    """
    return:
      (False, None)
      (True, ("AI_TP", price_now))
      (True, ("AI_SL", price_now))
      (True, ("AI_TIMEOUT", price_now))

    判定材料:
      - pct: 含み%（SELLは下落でプラスになるようPine側で変換済み）
      - mins_from_entry: 経過分
      - volume / vwap / atr
      - 銘柄別TP/SLしきい値(MODEL)で可変制御
    """

    if not position_dict or position_dict.get("closed"):
        return False, None

    ticks = position_dict.get("ticks", [])
    if not ticks:
        return False, None

    last = ticks[-1]

    sym         = position_dict.get("symbol")
    side        = position_dict.get("side")
    pct         = float(last.get("pct", 0) or 0)
    price_now   = float(last.get("price", 0) or 0)
    mins_open   = float(last.get("mins_from_entry", 0) or 0)
    vol_now     = float(last.get("volume", 0) or 0)
    atr_now     = float(last.get("atr", 0) or 0)
    vwap_now    = float(last.get("vwap", 0) or 0)

    # 最新モデルをロード（銘柄ごとの tp/sl）
    MODEL = _load_model()
    thresholds = MODEL.get(sym, {"tp": 1.0, "sl": -0.6})
    tp = float(thresholds.get("tp", 1.0))
    sl = float(thresholds.get("sl", -0.6))

    # 出来高×ATRがアツいときはもっと引っ張る(利確TPを強気に)
    if vol_now > 0 and atr_now > 0:
        heat = min(vol_now / (atr_now * 10000.0), 2.0)
        tp *= heat

    # VWAP割れたら損切りラインを浅く (SELLはVWAP上に戻ったら同じ)
    if side == "BUY" and vwap_now and price_now < vwap_now:
        sl = max(sl, -0.4)
    if side == "SELL" and vwap_now and price_now > vwap_now:
        sl = max(sl, -0.4)

    # 利確判定
    if pct >= tp:
        return True, ("AI_TP", price_now)

    # 損切り判定
    if pct <= sl:
        return True, ("AI_SL", price_now)

    # ダラけすぎてる場合の撤退
    if mins_open >= 15:
        return True, ("AI_TIMEOUT", price_now)

    return False, None
