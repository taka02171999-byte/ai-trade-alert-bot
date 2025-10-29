# ai_entry_logic.py
# ===============================
# 1) ENTRY_BUY/ENTRY_SELLを受け取った瞬間に
#    「即エントリー(real)にする？」or「保留(shadow_pending)にする？」を決める
#
# 2) shadow_pending中のやつを後から"正式エントリー"に昇格できるかどうかを判断する
# ===============================

def should_accept_entry(symbol, side, vol_mult, vwap, atr, last_pct):
    """
    入る/保留の一次判断。
    - vol_mult: 5分足の出来高 / 直近平均出来高 (例: 2.5 とか)
    - last_pct: 本気足(ブレイク元の5分足)の伸び率[%]
    - atr: ボラティリティ指標
    - vwap: 参考(今は判定に使わない)

    ロジック(今は仮):
      出来高スパイク + 明確な動き + ボラが極端じゃない
      → accept=True で即 "real"
      そうじゃない → shadow_pending で監視だけ
    """

    strong_vol    = vol_mult >= 1.8           # 出来高がしっかり入ってる
    trending_move = abs(last_pct) >= 0.25     # 方向性がちゃんと出てる
    atr_ok        = (atr == 0) or (0.3 <= atr <= 6.0)  # 超低ボラ/超異常ボラは避ける
    # vwap_condition = True  # 将来: BUYはVWAP上、SELLはVWAP下 とか入れる

    accept = strong_vol and trending_move and atr_ok

    if accept:
        reason = "出来高スパイク＋勢い確認→即エントリー採用"
    else:
        reason = "勢い/出来高が弱い→保留監視に回す"

    return accept, reason


def should_promote_to_real(position_dict):
    """
    shadow_pending のポジを「後追いで正式エントリー(real)昇格させる？」を判断。
    PRICE_TICKのたびにサーバーから呼ばれる。

    ざっくり基準:
      - 含み%がすでに+0.4%以上 (BUYでもSELLでも有利方向ならpctはプラスの設計)
      - 出来高がちゃんと入ってる
      - ATRがめちゃくちゃじゃない

    Trueが返ったら、server.py側で
      promote_to_real() + Discord「(後追い)エントリー」通知
    をやる。
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

    gain_ok = pct_now >= 0.4
    vol_ok  = vol_now > 0
    atr_ok  = (atr_now == 0) or (0.2 <= atr_now <= 8.0)

    return gain_ok and vol_ok and atr_ok
