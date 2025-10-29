# ai_entry_logic.py
# ===============================
# エントリー可否判定 + 銘柄ごとのしきい値読み込み対応
# ===============================
#
# 目的：
# - Pineはブレイク発生したらENTRY_BUY/ENTRY_SELLをサーバーに送る
# - ここで「即エントリー(real)でディスコード通知する？」or
#        「shadow_pendingで静かに後追い監視だけする？」を決める
#
# ポイント：
# - 銘柄ごとの基準値を data/entry_stats.json から読む
#   無い銘柄は初期値:
#     * break_pct      = 0.1 (%)
#     * vol_mult_req   = 2.0 (出来高の何倍なら信用できるか)
#
# - 今回は break_pct はまだ Pine 側に返していない（＝Pineは手動inputのまま）
#   だけど、将来ここで「この銘柄は最低0.2%抜けじゃないと信用しない」みたいにして
#   accept を絞ったりゆるめたりできるようにしてある
#
# - last_pct, atr も使って "勢いダメなら保留" の判定は今までどおり残す
#
# - should_promote_to_real() は変更なし（shadow_pendingを昇格する判定）
#

import os, json

ENTRY_STATS_PATH = "data/entry_stats.json"

# デフォ値（初日とか、統計まだない銘柄用）
DEFAULT_BREAK_PCT = 0.1        # % のブレイク幅の目安
DEFAULT_VOL_MULT_REQ = 2.0     # 出来高は平均の何倍ほしいか
DEFAULT_MIN_MOVE_PCT = 0.25    # last_pct: 最低このくらい走ってほしい (%)
DEFAULT_ATR_MIN = 0.3          # ボラが低すぎるのは除外
DEFAULT_ATR_MAX = 6.0          # ボラが狂いすぎるのも除外


def _load_entry_stats():
    """
    data/entry_stats.json を読む。
    {
      "7203.T": {
        "break_pct": 0.12,
        "vol_mult_req": 2.1
      },
      ...
    }
    無い場合は {} を返す。
    """
    if not os.path.exists(ENTRY_STATS_PATH):
        return {}
    try:
        with open(ENTRY_STATS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def _get_symbol_thresholds(symbol: str):
    """
    銘柄ごとのしきい値を返す。
    まだ学習データが無い銘柄はデフォルトを返す。
    """
    stats = _load_entry_stats()
    cfg = stats.get(symbol, {})
    return {
        "break_pct": float(cfg.get("break_pct", DEFAULT_BREAK_PCT)),
        "vol_mult_req": float(cfg.get("vol_mult_req", DEFAULT_VOL_MULT_REQ)),
    }


def should_accept_entry(symbol, side, vol_mult, vwap, atr, last_pct):
    """
    エントリーを即採用(real)するか、shadow_pendingで保留するかを決める。

    入力:
      symbol    : "7203.T" みたいな銘柄コード
      side      : "BUY" or "SELL"
      vol_mult  : Pine側で計算した「出来高スパイク倍率」
      vwap      : その時点のVWAP
      atr       : その時点のATR
      last_pct  : 「本気足」全体での伸び率[%] (BUY方向なら上に伸びた割合)
                  ※ Pine側から送ってくる用のやつを想定

    ロジック(今回版):
      1. 銘柄ごとの要求値を読む
         - vol_mult_req = この銘柄は最低これくらい出来高スパイクしてほしい
         - break_pct    = この銘柄はだいたいこのくらいのブレイク幅からしか素直に走らん
           (Pineのブレイク条件そのものはまだ変えないけど、
            break_pctが大きい銘柄は慎重にして accept を絞る、ってことはできる)

      2. 判定する
         - 「出来高ちゃんと入ってるか？」
         - 「ちゃんと走り出してるか？(last_pct >= X%)」
         - 「ATRが異常値じゃないか？」
         - 「その銘柄は普段ブレイク幅もっと深くないとダマシ多い銘柄じゃない？」→
            そういう銘柄は accept を厳しめにできる

      3. Trueなら"即エントリー採用"
         Falseなら"shadow_pending"で保留監視
    """

    # その銘柄のしきい値を取得（なければデフォルト）
    th = _get_symbol_thresholds(symbol)
    want_vol   = th["vol_mult_req"]     # 例: 2.0 (=平均の2倍の出来高ほしい)
    want_break = th["break_pct"]        # 例: 0.1 (%)

    # --- 1) 出来高スパイク判定
    cond_vol = float(vol_mult) >= want_vol

    # --- 2) 勢い判定（伸び率）
    # last_pct は「本気足でどれくらい伸びたか」
    cond_speed = abs(float(last_pct)) >= DEFAULT_MIN_MOVE_PCT

    # --- 3) ATR判定（低すぎ or 高すぎを弾く）
    atr_val = float(atr) if atr is not None else 0.0
    cond_atr = (atr_val >= DEFAULT_ATR_MIN) and (atr_val <= DEFAULT_ATR_MAX)

    # --- 4) ブレイク幅の許容チェック
    # ここは今後の拡張用「この銘柄はもっとちゃんと抜けてから入ったほうがいい」
    # Pine側の entry_break_pct は共通だけど、
    # たとえば want_break が 0.25% って学習されてる銘柄なのに
    # 今日は0.1%みたいな浅いブレイクでしか来てないなら、
    # 「それはダマシ率高いから本採用しないでshadowにしとこ」ってできる。
    #
    # いまはまだ Pine から "どれくらい上抜け/下抜けたかの実ブレイク率" をもらってないので、
    # とりあえず cond_break は常に True 扱いにしておく。
    # 次の段階で、Pineから `break_pct_hit` みたいなの送ったらここで使う。
    cond_break = True

    accept = cond_vol and cond_speed and cond_atr and cond_break

    if accept:
        reason = (
            "出来高スパイク & 勢いOK & ボラ適正 → 即エントリー採用\n"
            f"(銘柄別目安: 出来高≥{want_vol}倍 / 期待ブレイク幅≈{want_break}%)"
        )
    else:
        reason = (
            "まだ様子見（shadow監視）\n"
            f"出来高 {vol_mult:.2f}倍 vs 要求 {want_vol:.2f}倍 / "
            f"伸び率 {last_pct:.2f}% / ATR {atr_val:.2f}"
        )

    return accept, reason


def should_promote_to_real(position_dict):
    """
    shadow_pending を「後から正式エントリー(real)に昇格」させるかどうか。

    PRICE_TICKのたびに position_manager 側が保持してる
    position_dict["ticks"] の最新データを見る。

    今のルール:
      - pct_now >= +0.4% （= すでに有利方向にちゃんと伸びてる）
      - volume が それなりに入ってる (0じゃない)
      - ATRが極端じゃない
    これを満たしたら "これはもう実質入った扱いで良い" として
    その瞬間に Discordへ後追いエントリー通知を出すイメージ。
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
