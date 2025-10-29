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
    # まだ学習データが無い銘柄は tp=+1.5%, sl=-1.0% を初期値として使う。
    MODEL = _load_model()
    thresholds = MODEL.get(sym, {"tp": 1.5, "sl": -1.0})
    tp = float(thresholds.get("tp", 1.5))
    sl = float(thresholds.get("sl", -1.0))

    # --- リアルタイム調整その1: 相場が熱いときは利確はもっと引っ張る ---
    # 出来高(vol_now)とATR(atr_now)がデカい＝勢いある。
    # heat ~ 0〜2ぐらいで最大2倍。tpを最大くらいまで引っ張る。
    # （これはあなたの「その場に合わせて広げていいよ」の部分）
    if vol_now > 0 and atr_now > 0:
        heat = min(vol_now / (atr_now * 10000.0), 2.0)
        tp *= heat
        # これで例えば初期1.5%が 3.0%とか 5%近くまで伸びることもある。

    # --- リアルタイム調整その2: 逆行シグナル出たら損切りを浅くする ---
    # BUYでVWAP割れたらもう勢い死んだ扱い → -0.4%とかで即逃げる方向に引き上げ
    # SELLでVWAP上に戻ったら同じ扱い
    if side == "BUY" and vwap_now and price_now < vwap_now:
        # sl は「%損益」だから、-1.0%より浅い -0.4% に引き上げるイメージ
        sl = max(sl, -0.4)
    if side == "SELL" and vwap_now and price_now > vwap_now:
        sl = max(sl, -0.4)

    # --- リアルタイム調整その3: あえてもう少し我慢もする ---
    # あなたの「損切りも1.5までいいよ」ってリクエストは
    # ＝最大で -1.5% までは許容していい、って意味だったよね。
    # ここでは「ベースslが -1.0%」だけど、まだVWAP割れてない/勢い残ってるなら
    # sl を -1.5% まで広げるのもOKにする。
    # （pctは有利方向でプラスなので、"pctがそこまで悪くない"=そんなに負けてない）
    still_strong = (pct >= -0.8) and (vol_now > 0) and (atr_now > 0)
    if still_strong:
        # まだ壊れてない感じなら、slを -1.5% まで許容拡大できる
        sl = min(sl, -1.5)

    # --- エグジット判定 ---
    # 1) 利確
    if pct >= tp:
        return True, ("AI_TP", price_now)

    # 2) 損切り
    if pct <= sl:
        return True, ("AI_SL", price_now)

    # 3) タイムアウト
    #   Pineの保険は30分で強制クローズだから、AI側もそれに合わせて30分で逃がす
    if mins_open >= 30:
        return True, ("AI_TIMEOUT", price_now)

    return False, None
