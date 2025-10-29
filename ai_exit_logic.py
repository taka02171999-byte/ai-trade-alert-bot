# ai_exit_logic.py
# ===============================
# ポジション保有中の「利確/損切/タイムアウト」判断
# PRICE_TICKごとに呼ばれる
# ===============================

import os, json

MODEL_PATH = "data/ai_dynamic_thresholds.json"

def _load_model():
    if not os.path.exists(MODEL_PATH):
        return {}
    with open(MODEL_PATH, encoding="utf-8") as f:
        return json.load(f)

# モデル（銘柄別TP/SL閾値）
MODEL = _load_model()

def should_exit_now(position_dict):
    """
    return:
      (False, None)               → まだホールド
      (True, ("AI_TP", price_now)) → AI利確
      (True, ("AI_SL", price_now)) → AI損切り
      (True, ("AI_TIMEOUT", price_now)) → ダラけたので撤退

    判定材料:
      - pct: いまの含み%（SELLのときは下落でプラスになるようPine側でそろえてある）
      - mins_from_entry: 経過分
      - volume/vwap/atr で状況の熱さ・失速を判断
      - モデルで銘柄ごとのtp/slを可変
    """

    if not position_dict or position_dict.get("closed"):
        return False, None

    ticks = position_dict.get("ticks", [])
    if not ticks:
        return False, None

    last = ticks[-1]

    sym   = position_dict.get("symbol")
    side  = position_dict.get("side")
    pct   = float(last.get("pct", 0))
    price = float(last.get("price", 0))

    mins_open = float(last.get("mins_from_entry", 0) or 0)
    vol   = float(last.get("volume", 0) or 0)
    atr   = float(last.get("atr", 0) or 0)
    vwap  = float(last.get("vwap", 0) or 0)

    # この銘柄専用のTP/SLしきい値（学習結果あれば使う）
    thresholds = MODEL.get(sym, {"tp": 1.0, "sl": -0.6})
    tp = thresholds["tp"]
    sl = thresholds["sl"]

    # 出来高が強い＆ATRそこそこ→利幅もっと狙える（tp少し引き上げ）
    if vol > 0 and atr > 0:
        heat = min(vol / (atr * 10000.0), 2.0)  # バカみたいに跳ねないようcap
        tp *= heat

    # 逆行が始まってるなら損切りを浅く（-0.4%とか）に引き上げる
    if side == "BUY" and vwap and price < vwap:
        sl = max(sl, -0.4)
    if side == "SELL" and vwap and price > vwap:
        sl = max(sl, -0.4)

    # 判定
    if pct >= tp:
        return True, ("AI_TP", price)
    if pct <= sl:
        return True, ("AI_SL", price)
    if mins_open >= 15:
        return True, ("AI_TIMEOUT", price)

    return False, None
