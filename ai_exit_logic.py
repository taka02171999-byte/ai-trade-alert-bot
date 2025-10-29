# ai_exit_logic.py
#
# AI側のリアルタイム意思決定フック（拡張版）
# - shadow_pendingを昇格させるべきか？
# - そろそろクローズすべきか？
#
# Pine側仕様:
#   pct は BUYで上に行くと+、SELLで下に行くと+になるように揃ってる
#   mins_from_entry は昼休み抜きの経過分
#   vwap, atr, volume などもPRICE_TICKで貰える

def _get_last_tick(pos):
    ticks = pos.get("ticks", [])
    return ticks[-1] if ticks else None


def should_promote_to_real(position_dict):
    """
    shadow_pending → real に格上げするか？

    ざっくりロジック:
    - 現在のpctがある程度プラス方向（=順行してる）
    - 出来高それなりにある
    - 価格がVWAPより有利側に乗ってる (BUYなら上、SELLなら下)
      -> ここはPineからもらってる pct の定義的に、「pct>=0.4%」の時点で
         もう動いてるはずだから、それにVWAP判定を足すだけでもOK

    最初は単純にして、あとで学習ログを使って閾値を銘柄別に変える。
    """
    if not position_dict:
        return False

    if position_dict.get("status") != "shadow_pending":
        return False

    last = _get_last_tick(position_dict)
    if not last:
        return False

    pct_now = float(last.get("pct", 0.0))
    vwap    = last.get("vwap")
    price   = last.get("price")
    volume  = last.get("volume")

    # 最低ライン：ちゃんと順行中
    if pct_now < 0.4:
        return False

    # VWAPチェック:
    # BUYなら「現在値 >= vwap」、SELLなら「現在値 <= vwap」だと素直
    side = position_dict.get("side", "BUY")
    if vwap is not None and price is not None:
        if side == "BUY" and not (price >= vwap):
            return False
        if side == "SELL" and not (price <= vwap):
            return False

    # 出来高があまりにもスカスカなら弾きたい、ただしまだ閾値は強くしない
    # volumeが0とか None ならスキップする
    if volume is not None:
        try:
            volf = float(volume)
            if volf <= 0:
                return False
        except:
            pass

    return True


def should_exit_now(position_dict):
    """
    AIが「今このポジ切るべき？」を返す。

    今回の改良ポイント：
    - pct_nowだけじゃなく、VWAP割れ/超え崩れを警戒して早めに損切りできるようにする
    - ATRが高い(=ボラ高い)ときは利確ラインを少し緩めてもいいし、
      逆にATRが低いのに全然伸びないなら早めに諦める、みたいな調整の入口を用意する

    戻り値:
      wants_exit(bool),
      (exit_type:str, exit_price:float) or None
    """

    if (not position_dict) or position_dict.get("closed"):
        return False, None

    last = _get_last_tick(position_dict)
    if not last:
        return False, None

    pct_now = _safe_float(last.get("pct"))
    price_now = _safe_float(last.get("price"))
    mins_from_entry = _safe_float(last.get("mins_from_entry"))
    vwap = _safe_float(last.get("vwap"))
    atr  = _safe_float(last.get("atr"))

    side = position_dict.get("side", "BUY")

    # ---------- 1. ハード利確 ----------
    # ベースライン: +1.0%以上で利確
    take_profit_level = 1.0

    # ATRが低い = あまり伸びない銘柄なら、+0.8%でもOKにしてすぐ確定してもいい
    if atr is not None and atr < 5:  # 数字は仮：ボラ低い小動き銘柄イメージ
        take_profit_level = 0.8

    if pct_now is not None and pct_now >= take_profit_level:
        return True, ("AI_TP", price_now)

    # ---------- 2. ハード損切り ----------
    # ベースライン: -0.6%で損切り
    stop_loss_level = -0.6

    # もしもうVWAPを逆側に割り込んでるなら、ちょいでも不利なら逃げる
    # - BUYで price < vwap は弱い
    # - SELLで price > vwap は弱い（=戻されてる）
    if vwap is not None and price_now is not None:
        if side == "BUY" and price_now < vwap:
            stop_loss_level = -0.3
        if side == "SELL" and price_now > vwap:
            stop_loss_level = -0.3

    if pct_now is not None and pct_now <= stop_loss_level:
        return True, ("AI_SL", price_now)

    # ---------- 3. タイムアウト ----------
    # ベースライン: 15分経過で撤退
    timeout_minutes = 15.0

    # ATRがスーパーハイなら、もうちょい粘ることもあり得る
    # 例えばatrが20とか30とか、とにかく動きまくってる銘柄は
    # 「15分で終わり」は早すぎることがあるので20分まで許容、とか。
    if atr is not None and atr > 20:
        timeout_minutes = 20.0

    if mins_from_entry is not None and mins_from_entry >= timeout_minutes:
        return True, ("AI_TIMEOUT", price_now)

    return False, None


def _safe_float(x):
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None
