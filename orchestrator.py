# orchestrator.py
# ===============================
# エントリー/クローズ周りの軽い管理。
#
# もともとは:
#   - active_symbols に今監視中の銘柄を入れる
#   - クローズ後に同じ銘柄へすぐ再エントリーしないように cooldown する
#
# 今回の版では、あなたの希望どおり
# 「同じ銘柄でもすぐまた入ってOK」にするため、
# クールダウン機能は実質オフにしてある。
#
# server.py 側は
#   orchestrator.mark_symbol_active(symbol)
#   orchestrator.mark_symbol_closed(symbol)
# を呼んでくるけど、
# それらは今も安全に動くようにしてある。
#
# should_accept_signal() も残してあるけど、
# server.py では使っていないので特に影響なし。
# ===============================

import os
import json
from datetime import datetime, timedelta

TOP_LIMIT = int(os.getenv("TOP_SYMBOL_LIMIT", "10"))

ORCH_STATE_PATH = "data/orchestrator_state.json"


def _utc_now():
    return datetime.utcnow()


def _now_iso():
    return _utc_now().isoformat(timespec="seconds")


def load_orch():
    """
    orchestrator_state.json の現在の状態を読む。
    フォーマット例:
    {
        "active_symbols": ["7203.T","6758.T", ...],
        "cooldown": {
            "7203.T": "2025-10-25T10:00:00"
        }
    }
    cooldownは今回は無効化するけど、互換性のために残してある。
    """
    if not os.path.exists(ORCH_STATE_PATH):
        return {
            "active_symbols": [],
            "cooldown": {}
        }
    with open(ORCH_STATE_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            # 壊れてたら初期状態で返す
            return {
                "active_symbols": [],
                "cooldown": {}
            }


def save_orch(state):
    os.makedirs(os.path.dirname(ORCH_STATE_PATH), exist_ok=True)
    with open(ORCH_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ===============================
# クールダウン関連（今回は無効化）
# ===============================

def is_cooldown(symbol, orch_state):
    """
    クールダウン中かどうかを判定する関数。
    以前は "cooldown" 辞書の時刻を見て True/False を返していた。

    今回は「同じ銘柄でも即もう一回入ってOK」にしたいので
    常に False を返すようにする。
    """
    return False


def put_cooldown(symbol, minutes=5):
    """
    以前はここで cooldown に '今+5分' を入れてた。
    今回はクールダウン機能オフなので、何もしない。
    """
    # no-op: cooldownを記録しない
    return


# ===============================
# active_symbols管理
# ===============================

def refresh_top_symbols():
    """
    active_symbols が変に増えすぎたら TOP_LIMIT 件までに縮める。
    （古い後ろのやつを落とすイメージ）

    ここは今まで通り残す。
    """
    orch_state = load_orch()
    active = orch_state.get("active_symbols", [])
    orch_state["active_symbols"] = active[:TOP_LIMIT]
    save_orch(orch_state)


def mark_symbol_active(symbol):
    """
    server.py のENTRY時に呼ばれる。
    監視中シンボルリスト(active_symbols)の先頭に突っ込む。
    """
    orch_state = load_orch()

    if "active_symbols" not in orch_state:
        orch_state["active_symbols"] = []
    if "cooldown" not in orch_state:
        orch_state["cooldown"] = {}

    # すでに入ってたら一回消してから先頭に入れる（優先度を一番上に）
    if symbol in orch_state["active_symbols"]:
        orch_state["active_symbols"].remove(symbol)
    orch_state["active_symbols"].insert(0, symbol)

    # リストがデカくなりすぎないように整える
    orch_state["active_symbols"] = orch_state["active_symbols"][:TOP_LIMIT]

    save_orch(orch_state)


def mark_symbol_closed(symbol):
    """
    server.py のEXIT時に呼ばれる。

    以前は:
      - put_cooldown(symbol, minutes=5) でクールダウン入れてた
    今回は:
      - クールダウン廃止なので何もしない（記録だけしておしまい）

    将来的に「この銘柄はしばらく危険だから避けろ」とかやりたくなったら、
    ここにロジックを復活させればいい。
    """
    orch_state = load_orch()
    # active_symbols からは消さない。むしろ残しといてOK。
    # cooldown も書かない。
    save_orch(orch_state)


# ===============================
# エントリー可否チェック (将来用)
# ===============================

def should_accept_signal(symbol, side):
    """
    昔の構想では、ここで
      - クールダウン中なら拒否
      - active_symbolsに入ってるか
      - ここでTP/SL目安返す
    みたいなフィルタをかけてサーバーのENTRYを決める想定だった。

    でも今の server.py は ai_entry_logic.should_accept_entry() で判断してるので
    実際にはこの関数は呼ばれていない。

    一応インターフェースだけ残しておく。
    """
    orch_state = load_orch()
    refresh_top_symbols()

    # クールダウンは常にFalseなのでここでは無視
    # in_top も現状は意味だけ残す
    in_top = True  # 今は基本ぜんぶ通す

    if not in_top:
        return False, "not_in_top", None, None

    # TP/SL目安はまだ学習してないので None
    tp_target = None
    sl_target = None

    return True, "", tp_target, sl_target
