import os
import json
from datetime import datetime, timedelta

# 状態ファイル
ORCH_STATE_PATH = "data/orchestrator_state.json"

# 何分クールダウンさせるか（同じ銘柄を連打しないように）
DEFAULT_COOLDOWN_MIN = 5

# いちおうアクティブ銘柄の上限。多すぎると監視がカオスになるので切り詰める
TOP_LIMIT = int(os.getenv("TOP_SYMBOL_LIMIT", "10"))


def _utc_now():
    return datetime.utcnow()


def _now_iso():
    return _utc_now().isoformat(timespec="seconds")


def _load_state():
    """
    orchestrator_state.json の形:
    {
      "active_symbols": ["7203.T", "6758.T", ...],
      "cooldown": {
         "7203.T": "2025-10-26T08:15:00",
         ...
      }
    }
    無ければ初期状態を返す。
    """
    if not os.path.exists(ORCH_STATE_PATH):
        return {
            "active_symbols": [],
            "cooldown": {}
        }
    try:
        with open(ORCH_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {
            "active_symbols": [],
            "cooldown": {}
        }


def _save_state(state: dict):
    os.makedirs(os.path.dirname(ORCH_STATE_PATH) or ".", exist_ok=True)
    with open(ORCH_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _is_on_cooldown(symbol: str, st: dict) -> bool:
    """
    クールダウン中なら True
    """
    cd = st.get("cooldown", {})
    if symbol not in cd:
        return False

    until_str = cd[symbol]
    try:
        until_dt = datetime.fromisoformat(until_str)
    except:
        # パースできないならクールダウン扱いしないで解除しちゃう
        return False

    return _utc_now() < until_dt


def _set_cooldown(symbol: str, minutes: int = DEFAULT_COOLDOWN_MIN):
    """
    この銘柄をしばらく触らないようにクールダウンする
    """
    st = _load_state()
    st.setdefault("cooldown", {})
    st["cooldown"][symbol] = (_utc_now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    _save_state(st)


def _refresh_top_symbols(st: dict):
    """
    active_symbolsをTOP_LIMIT件に切り詰めるだけの単純な優先度管理。
    先頭がよりホットなやつ。
    """
    active_list = st.get("active_symbols", [])
    st["active_symbols"] = active_list[:TOP_LIMIT]


def mark_symbol_active(symbol: str):
    """
    その銘柄を「いま監視中/稼働中のホットな銘柄」として active_symbols の先頭に入れる。
    - ENTRY直後
    - shadow_pending→realに昇格した瞬間
    で呼んでる。
    """
    st = _load_state()

    # 既に入ってたら一旦消して、先頭に入れ直す（優先度UP）
    active = st.get("active_symbols", [])
    if symbol in active:
        active.remove(symbol)
    active.insert(0, symbol)
    st["active_symbols"] = active

    # 一応クールダウン解除されてても問題ない、放置でOK
    # （もしクールダウン中にまたシグナルが来たらAIが決めるので）

    _refresh_top_symbols(st)
    _save_state(st)


def mark_symbol_closed(symbol: str):
    """
    決済が完了したときに呼ぶ。
    - クールダウンをセットして、すぐ再エントリー連打しにくくする
    """
    _set_cooldown(symbol, minutes=DEFAULT_COOLDOWN_MIN)

    # active_symbolsから消すかどうかは運用次第。
    # ここで消しておく。
    st = _load_state()
    if symbol in st.get("active_symbols", []):
        st["active_symbols"].remove(symbol)
    _refresh_top_symbols(st)
    _save_state(st)


# ===== （注意） =====
# 前バージョンでは server.py 側が ENTRY受信のときに
# orchestrator.should_accept_signal() を呼んで
# "active_symbolsに入ってなきゃ拒否" とかやってた。
#
# 今の最終フローではエントリー可否は ai_entry_logic.should_accept_entry() に
# 100%任せてるから、こっちはもう使わない。
#
# ただ、将来この関数をまた呼びたくなった時に落ちないように
# ダミーで置いておく。
def should_accept_signal(symbol: str, side: str):
    """
    互換目的のダミー。
    いまはAI判断が優先なので、基本常に受け入れる側にする。
    tp/sl目標はここでは決めていないので None。
    """
    st = _load_state()

    # クールダウン中なら「まぁ一応警戒」って扱いにしておきたいならここでFalse返す。
    # ただし今のフローでは ai_entry_logic がメイン意思決定者なので、
    # ここではTrueを返してサーバーのロジックとケンカしないようにする。
    on_cd = _is_on_cooldown(symbol, st)
    if on_cd:
        return False, "cooldown", None, None

    return True, "", None, None
