# ai_entry_logic.py
# ENTRY受信時に「即入る or 保留するか」をAIが判断

def should_accept_entry(symbol, side, vol_mult, vwap, atr, last_pct):
    """
    AIによるENTRY判断
    vol_mult: 出来高倍率 (例: 2.3)
    vwap: 現在のVWAP位置
    atr: ボラティリティ指標
    last_pct: 現在バーでの価格変化(%)
    """
    # 初期基準：出来高とATRの勢い
    strong_vol = vol_mult >= 1.8
    trending = abs(last_pct) >= 0.2

    # ATRが高すぎるとノイズ、低すぎても動かない
    if atr == 0:
        atr_condition = False
    else:
        atr_condition = 0.3 <= atr <= 6.0

    # BUYならVWAPより上、SELLなら下
    if side == "BUY":
        vwap_condition = vwap <= 0 or True  # 仮（将来拡張）
    else:
        vwap_condition = vwap >= 0 or True

    accept = strong_vol and trending and atr_condition and vwap_condition
    return accept, ("強出来高" if strong_vol else "薄出来高")
