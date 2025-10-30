# ai_entry_logic.py
# ===============================
# エントリー可否の一次判定ロジック
# TradingViewのENTRY_*受信時に呼ばれる
# ===============================

import os, json

ENTRY_MODEL_PATH = "data/entry_stats.json"

DEFAULT_BREAK_PCT   = 0.05   # 初期ブレイク幅(%) 0.1%
DEFAULT_VOL_REQ     = 2.0   # 初期の出来高倍率しきい値 (平均の2倍以上ほしい)
DEFAULT_ATR_MIN     = 0.3   # ボラ低すぎ除外
DEFAULT_ATR_MAX     = 30.0   # ボラ高すぎ除外
DEFAULT_TREND_ABS_P = 0.25  # その足が最低これくらいは動いててほしい(%)

def _load_entry_model():
    """
    銘柄ごとのエントリーしきい値ファイルを読む。
    例:
    {
      "7203.T": { "break_pct": 0.09, "vol_mult_req": 1.7 },
      "9984.T": { "break_pct": 0.13, "vol_mult_req": 2.4 }
    }

    無かったら空{}返す。
    """
    if not os.path.exists(ENTRY_MODEL_PATH):
        return {}
    try:
        with open(ENTRY_MODEL_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def should_accept_entry(symbol, side, vol_mult, vwap, atr, last_pct):
    """
    入る/保留の判断をする（即Discordで「エントリー確定🟢」出すか、shadowで保留か）。
    実際は:
      - 銘柄ごとの学習済み閾値 (entry_stats.json) があればそれを優先
      - 無ければデフォルト値で判定

    引数:
      symbol    : "7203.T" とか
      side      : "BUY"/"SELL"
      vol_mult  : その5分足出来高 / 平均出来高
      vwap      : その時点のVWAP（今は深く使ってない）
      atr       : ATR(14) みたいなボラ目安
      last_pct  : 本気足の伸び率(%)、ざっくり勢い
    """

    model = _load_entry_model()
    per_symbol = model.get(symbol, {})

    # 学習済みがあればそれ、なければデフォルト
    vol_req   = float(per_symbol.get("vol_mult_req", DEFAULT_VOL_REQ))
    brk_req   = float(per_symbol.get("break_pct",    DEFAULT_BREAK_PCT))

    # --- 基本ルール ---
    strong_vol     = vol_mult >= vol_req              # 出来高ちゃんと入ってるか
    trending       = abs(last_pct) >= DEFAULT_TREND_ABS_P  # ちゃんと走ってる足か
    atr_condition  = (atr == 0) or (DEFAULT_ATR_MIN <= atr <= DEFAULT_ATR_MAX)

    # brk_req は「この銘柄はこのくらいのブレイクで勝ててる」って学習値
    # last_pct は実際今回の足の伸び率(勢い)。方向性が近いかざっくり見る。
    # BUYなら上方向、SELLなら下方向を期待するので、
    #   BUY: last_pct 正方向にそこそこ出てる？
    #   SELL: last_pct 負方向(=下げ)がそこそこ出てる？
    # ただし last_pct は"abs"でもすでに trending 判定済だからここでは強めに絞らない。
    breakout_ok    = abs(last_pct) >= brk_req

    accept = strong_vol and trending and atr_condition and breakout_ok

    if accept:
        reason = (
            "出来高/勢い/ボラOK→即エントリー採用\n"
            f"(要求vol≧{vol_req:.2f}x, 要求ブレイク≧{brk_req:.3f}%)"
        )
    else:
        reason = (
            "条件が弱いので保留監視（shadow）\n"
            f"(要求vol≧{vol_req:.2f}x, 要求ブレイク≧{brk_req:.3f}%)"
        )

    return accept, reason


def should_promote_to_real(position_dict):
    """
    shadow_pendingを「後追いで正式採用(real)に昇格させるか？」を判定する。
    PRICE_TICKのたびにサーバー側で呼ぶ準備になってる。
    ここではとりあえず pct + 出来高がちゃんと乗ってるかだけ見る。
    """
    if not position_dict or position_dict.get("closed"):
        return False
    if position_dict.get("status") != "shadow_pending":
        return False

    ticks = position_dict.get("ticks", [])
    if not ticks:
        return False

    last_tick = ticks[-1]

    pct_now  = float(last_tick.get("pct", 0) or 0)
    vol_now  = float(last_tick.get("volume", 0) or 0)
    atr_now  = float(last_tick.get("atr", 0) or 0)

    # 条件イメージ:
    #   - 既に+0.4%以上自分に有利方向で動いてる
    #   - 出来高0じゃない(一応流動性ある)
    #   - ATRが極端に死んでない
    gain_ok = pct_now >= 0.4
    vol_ok  = vol_now > 0
    atr_ok  = (atr_now == 0) or (0.2 <= atr_now <= 8.0)

    return gain_ok and vol_ok and atr_ok
