# ai_model_trainer.py
# ===============================
# 1. エグジット学習:
#    data/learning_log.jsonl から
#    銘柄別の "どこで利確/損切りしたのが現実的か" を学習し、
#    data/ai_dynamic_thresholds.json に書く
#
# 2. エントリー学習:
#    同じ learning_log.jsonl から
#    「どんなブレイク幅 / 出来高倍率で入ったとき勝ちやすかったか」
#    を銘柄別に集計して
#    data/entry_stats.json に書く
#
# このファイルを定期的に叩けばどっちも自動で更新される。
# サーバー側やPine側はそのファイルを読むだけで
# 翌日からもう反映される。
# ===============================

import os, json, statistics

LEARN_PATH = "data/learning_log.jsonl"

TP_SL_MODEL_PATH   = "data/ai_dynamic_thresholds.json"  # 利確/損切り用
ENTRY_MODEL_PATH   = "data/entry_stats.json"            # エントリー判定用

# ---------------------------
# ユーティリティ
# ---------------------------

def _safe_float(v, default=None):
    try:
        return float(v)
    except:
        return default

def _load_learning_rows():
    """
    learning_log.jsonl を全部読む。
    各行は position_manager.force_close() 時に吐かれる想定で、こんな感じ:
    {
      "symbol": "7203.T",
      "side": "BUY",
      "status": "real",
      "entry_price": 3012.0,
      "close_price": 3045.0,
      "final_pct": 1.05,
      "close_reason": "AI_TP",
      "ticks": [
          { "pct": 0.3, "volume": 123400, "atr": 1.2, ... },
          ...
      ]
    }
    """
    rows = []
    if not os.path.exists(LEARN_PATH):
        return rows
    with open(LEARN_PATH, "r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                rows.append(row)
            except:
                continue
    return rows

def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

# ---------------------------
# 1. EXIT側モデル更新
# ---------------------------

def train_dynamic_thresholds():
    """
    銘柄ごとに TP/SL/Timeout の「ちょうどいいライン」を学習して
    ai_dynamic_thresholds.json に保存する。
    """
    rows = _load_learning_rows()
    per_symbol_returns = {}  # sym -> [final_pct,...]

    for r in rows:
        sym = r.get("symbol")
        final_pct = _safe_float(r.get("final_pct"), None)
        if sym is None or final_pct is None:
            continue
        per_symbol_returns.setdefault(sym, []).append(final_pct)

    model = {}
    for sym, vals in per_symbol_returns.items():
        avg = statistics.mean(vals)
        std = statistics.pstdev(vals) if len(vals) > 1 else 0.3

        # だいたいこのくらいで利確しておくと良かった？という閾値(tp)
        # だいたいこれくらい悪化したらやばかった、っていう下限(sl)
        tp_line = round(avg + std * 1.2, 2)
        sl_line = round(avg - std * 1.5, 2)

        # セーフティ: 初期のデフォルト帯から大きく離れすぎないように軽くクリップしておくこともできる
        # ここでは特に制限なし。あなたの運用で必要なら追加してOK。

        model[sym] = {
            "tp": tp_line,
            "sl": sl_line,
        }

    _write_json(TP_SL_MODEL_PATH, model)
    return model

# ---------------------------
# 2. ENTRY側モデル更新
# ---------------------------

def train_entry_thresholds():
    """
    銘柄ごとに「どういう条件でエントリーしたら勝ちやすかったか」を集計。
    その結果を entry_stats.json に保存する。

    出したいもの:
      - vol_mult_req: 最低限ほしい出来高倍率
      - break_pct   : 最低限ほしいブレイク勢い(%)

    考え方:
      - status=="real" だったポジション、かつ最終的にプラスで終わったやつだけ見る
        → 「AIが実際入って勝った形」をサンプルにする
      - そのポジションの ticks の最初の数本から、
        だいたいの初動の pct / volume / atr を拾う
        （ここは簡略化：最初のtickだけ見る）
    """

    rows = _load_learning_rows()

    per_symbol_samples = {}  # sym -> [{"pct":..., "vol":..., "atr":...}, ...]

    for r in rows:
        sym = r.get("symbol")
        status = r.get("status")          # "real" or "shadow_pending"
        final_pct = _safe_float(r.get("final_pct"), None)

        # 勝ちトレだけ学習に使う（final_pct>0）
        if sym is None or final_pct is None or final_pct <= 0:
            continue
        # shadow_pendingで終わった(=本採用されてない)やつは除外
        if status != "real":
            continue

        ticks = r.get("ticks", [])
        if not ticks:
            continue

        first_tick = ticks[0]
        pct0 = _safe_float(first_tick.get("pct"), None)
        vol0 = _safe_float(first_tick.get("volume"), None)
        atr0 = _safe_float(first_tick.get("atr"), None)

        # 初動pctが小さすぎたりNoneなら無視
        if pct0 is None or vol0 is None or atr0 is None:
            continue

        per_symbol_samples.setdefault(sym, []).append({
            "pct": pct0,
            "vol": vol0,
            "atr": atr0,
        })

    entry_model = {}
    for sym, arr in per_symbol_samples.items():
        # ざっくり「このくらいは欲しいよね」という下限値を決める。
        # - break_pct : 初動pctの下側30%タイルくらい (=かなり控えめに見てもこれぐらいは動いてた)
        # - vol_mult_req : volumeは絶対値だから倍率じゃないけど、今は相対情報が無いので
        #   ひとまず中央値から「これ以上欲しい」みたいに使う
        #
        # シンプルに平均 - ちょいマージン でもいい。とりあえず平均ベースで作る。

        avg_pct = statistics.mean([x["pct"] for x in arr])
        avg_vol = statistics.mean([x["vol"] for x in arr])

        # 最低ブレイク幅は過去勝ちパターンの平均pctの80%くらいを要求してみる
        learned_break = round(avg_pct * 0.8, 3)

        # volumeしきい値は「勝った時これくらいの出来高だった」ってやつの80%
        # ただしあなたのPine側は vol_mult (平均比〇倍) を送るので、
        # 本当はこっちも vol_mult を直接学習するのがベスト。
        # いまのPineが生で vol_mult を送るようにしてくれてればここはそのまま使える。
        # もし volume 生値しかない場合は、そのままavg_volを要求値にするしかない。
        # → 今回は vol_mult がENTRYで送られる前提なので avg_volを'倍率'として扱う。
        learned_vol = round(avg_vol * 0.8, 3)

        # 安全装置: 最低ラインとしてデフォルトよりは下に行きすぎないようにちょいmax()
        # 初期値: break_pct=0.1, vol_mult_req=2.0
        if learned_break < 0.05:
            learned_break = 0.05
        if learned_vol < 1.2:
            learned_vol = 1.2

        entry_model[sym] = {
            "break_pct": learned_break,
            "vol_mult_req": learned_vol
        }

    # すでにあるやつを読み込んでマージ（新しい学習結果で上書き、なかった銘柄は追加）
    old = {}
    if os.path.exists(ENTRY_MODEL_PATH):
        try:
            with open(ENTRY_MODEL_PATH, "r", encoding="utf-8") as f:
                old = json.load(f)
        except:
            old = {}

    merged = old.copy()
    merged.update(entry_model)

    _write_json(ENTRY_MODEL_PATH, merged)

    return merged
