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
      - pct              : 含み%（SELLは下落でプラスになるようPine側で正規化済み）
      - mins_from_entry  : 経過分
      - volume / vwap / atr
      - 銘柄別TP/SLしきい値(MODEL)でのベースライン
      - その場の熱さ(出来高×ATR)でTPを引っ張る
      - VWAP割れたらSLを浅くする など
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

    # --- ベースラインTP/SLを決める ---
    # 学習済みモデルがあればそれを使う。
    # まだ学習データが無い銘柄は
    #   tp = +3.0%
    #   sl = -1.5%
    # を初期値として使う。（あなたの希望値）
    MODEL = _load_model()
    thresholds = MODEL.get(sym, {"tp": 3.0, "sl": -1.5})
    tp = float(thresholds.get("tp", 3.0))
    sl = float(thresholds.get("sl", -1.5))

    # --- リアルタイム調整その1: 相場が熱いときは利確はもっと引っ張る ---
    # 出来高(vol_now)とATR(atr_now)がデカい＝勢いある。
    # heat ~ 0〜2ぐらいで最大2倍。tpを最大くらいまで引っ張る。
    # → つまりベース3%を、状況次第で4〜6%以上まで許容する感じ。
    if vol_now > 0 and atr_now > 0:
        heat = min(vol_now / (atr_now * 10000.0), 2.0)
        tp *= heat
        # ここで tp がベースよりさらに上振れするのはOK。
        # 「そこから何％上げたりはAIの自由」に該当。

    # --- リアルタイム調整その2: 逆行シグナル出たら損切りを浅くする ---
    # BUYでVWAP割れたら勢い死んだ扱い → -0.4%くらいで逃げ方向
    # SELLでVWAP上に戻ったら同様。
    # これは「AIが勝手に浅くする」側の裁量ね。
    if side == "BUY" and vwap_now and price_now < vwap_now:
        # sl は「%損益」だから、-1.5%を -0.4%まで引き上げることもある
        sl = max(sl, -0.4)
    if side == "SELL" and vwap_now and price_now > vwap_now:
        sl = max(sl, -0.4)

    # --- リアルタイム調整その3: あえて少し我慢もする ---
    # まだ崩れてないなら、slをちょい深めに許容するロジック。
    # ここ、もともとは -1.0%ベースで「-1.5%まで許容」ってしてたけど、
    # いまベースがすでに -1.5% だから、
    # 「さらに深くする」っていう必要は基本なしでいい。
    #
    # ただ、あなたのニュアンスは
    #   「基礎値は-1.5。ただしAIが浅くするのはOKだが深くする方向は別に伸ばさなくていい」
    # だったので、ここは 'sl をもっと悪化方向に拡大' はもうやらない。
    #
    # ＝still_strongロジックは緩和し、slを深掘り(例えば-2.0とか)にはしない。
    still_strong = (pct >= -0.8) and (vol_now > 0) and (atr_now > 0)
    # 以前は:
    #   if still_strong:
    #       sl = min(sl, -1.5)
    # いまはベースが-1.5なので、何もしないで維持
    _ = still_strong  # just to keep the variable used

    # --- エグジット判定 ---
    # 1) 利確
    if pct >= tp:
        return True, ("AI_TP", price_now)

    # 2) 損切り
    if pct <= sl:
        return True, ("AI_SL", price_now)

    # 3) タイムアウト
    #   Pine側の保険は30分で強制クローズだから、AI側も30分で逃がす
    if mins_open >= 30:
        return True, ("AI_TIMEOUT", price_now)

    return False, None
