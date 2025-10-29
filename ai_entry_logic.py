# ai_entry_logic.py
# ===============================
# エントリー可否の一次判定ロジック
# TradingViewのENTRY_*受信時に呼ばれる
# ここで「即エントリー(real)」or「保留(shadow_pending)」か決める
# ===============================

def should_accept_entry(symbol, side, vol_mult, vwap, atr, last_pct):
    """
    入る/保留の判断をする。
    - vol_mult: その5分足の出来高が平均の何倍か
    - last_pct: その本気足の伸び率(=勢い)
    - atr: ボラの目安
    現状は勢いと出来高がちゃんと乗ってるやつを即エントリー、それ以外はshadow_pending。
    """
    strong_vol = vol_mult >= 1.8        # 出来高が平均の1.8倍以上
    trending = abs(last_pct) >= 0.25    # 足全体で0.25%以上動いてる
    atr_condition = 0.3 <= atr <= 6.0   # 低ボラすぎ/狂いすぎ除外
    vwap_condition = True               # 今は常にTrue。あとでBUY/VWAP上とか入れる

    accept = strong_vol and trending and atr_condition and vwap_condition

    if accept:
        reason = "出来高スパイク＋勢いあり → 即エントリー採用"
    else:
        reason = "勢い/出来高が弱いので保留監視（shadow）"

    return accept, reason


def should_promote_to_real(position_dict):
    """
    shadow_pendingを「後追いで正式採用(real)に昇格させるか？」を判定する。
    PRICE_TICKのたびに呼ばれる。

    ざっくりロジック：
    - 含み率pctがすでに+0.4%以上有利に動いてる（BUYもSELLもpctは有利方向で+になる前提）
    - 出来高がそれなりに入っている
    - すでに大きく逆行していない
    """
    if not position_dict or position_dict.get("closed"):
        return False

    if position_dict.get("status") != "shadow_pending":
        return False

    ticks = position_dict.get("ticks", [])
    if not ticks:
        return False

    last_tick = ticks[-1]

    pct_now = float(last_tick.get("pct", 0) or 0)
    vol_now = float(last_tick.get("volume", 0) or 0)
    atr_now = float(last_tick.get("atr", 0) or 0)

    # 条件：
    # ちょい走りだした (+0.4% 以上) かつ 出来高ちゃんと入ってる かつ ATRが極端じゃない
    gain_ok = pct_now >= 0.4
    vol_ok  = vol_now > 0
    atr_ok  = (atr_now == 0) or (0.2 <= atr_now <= 8.0)

    return gain_ok and vol_ok and atr_ok
