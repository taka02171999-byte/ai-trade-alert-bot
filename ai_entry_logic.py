# ai_entry_logic.py
# ===============================
# エントリー可否の一次判定ロジック
# TradingViewのENTRY_*受信時に呼ばれる
# ここで「即エントリー(ok=real)」か「保留(shadow_pending)」か決める
# ===============================

def should_accept_entry(symbol, side, vol_mult, vwap, atr, last_pct):
    """
    入る/保留の判断。
    - vol_mult: その5分足の出来高が平均の何倍か
    - last_pct: 足の伸び率(ブレイクの勢い)
    - atr: ボラの目安
    なるべく「勢いあってちゃんと動いてるやつだけ」リアルで入る。
    それ以外はshadow_pending(保留監視＆学習対象)に回す。
    """
    strong_vol = vol_mult >= 1.8        # 出来高が平均の1.8倍以上
    trending = abs(last_pct) >= 0.25    # 足自体に0.25%以上の伸び/落ち
    atr_condition = 0.3 <= atr <= 6.0   # さすがにガチ低ボラや無茶苦茶高ボラは弾く
    # vwap_condition は今は常にTrueだけど、あとで
    #   BUY → 現値がVWAPより上 じゃないとダメ
    #   SELL → 現値がVWAPより下 じゃないとダメ
    # みたいにすぐ拡張できるようにしてある
    vwap_condition = True

    accept = strong_vol and trending and atr_condition and vwap_condition

    if accept:
        reason = "出来高スパイク＋勢いあり → 即エントリー採用"
    else:
        reason = "勢い/出来高が弱いので保留監視（shadow）"

    return accept, reason
