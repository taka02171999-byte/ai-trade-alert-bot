import os
import json
from datetime import datetime, timedelta

TOP_LIMIT = int(os.getenv("TOP_SYMBOL_LIMIT", "10"))
ORCH_STATE_PATH = "data/orchestrator_state.json"

def _utc_now():
    return datetime.utcnow()

def _utc_iso(dt=None):
    if dt is None:
        dt = _utc_now()
    return dt.isoformat(timespec="seconds")

def load_orch():
    # orchestrator_state.json が無い or 壊れてる場合でも壊れず動くように
    base = {
        "active_symbols": [],  # 現在エントリー対象と考えていい銘柄
        "cooldown": {}         # { "7203.T": "2025-10-26T06:30:00" }
    }
    if not os.path.exists(ORCH_STATE_PATH):
        return base
    try:
        with open(ORCH_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "active_symbols" not in data:
                data["active_symbols"] = []
            if "cooldown" not in data:
                data["cooldown"] = {}
            return data
    except:
        return base

def save_orch(state):
    with open(ORCH_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def is_cooldown(symbol, orch_state):
    """
    クールダウン中ならTrue
    """
    cd = orch_state.get("cooldown", {})
    if symbol not in cd:
        return False
    try:
        until_dt = datetime.fromisoformat(cd[symbol])
    except:
        return False
    return _utc_now() < until_dt

def put_cooldown(symbol, minutes=5):
    """
    クローズ直後に少し冷却時間を与える。
    """
    orch_state = load_orch()
    orch_state.setdefault("cooldown", {})
    orch_state["cooldown"][symbol] = (_utc_now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    save_orch(orch_state)

def refresh_top_symbols():
    """
    active_symbolsをTOP_LIMIT以内に丸める。

    本番イメージ：
    - utils/ai_selector.pyで銘柄スコアを出す
    - スコア上位をactive_symbolsに入れる
    - あと勝率や出来高、ボラ、最近のヒット率で並べ替える

    いまはシンプルに「既にactive_symbolsに入ってる順番を維持、頭からTOP_LIMITだけ有効」にしてる。
    """
    orch_state = load_orch()
    active = orch_state.get("active_symbols", [])
    orch_state["active_symbols"] = active[:TOP_LIMIT]
    save_orch(orch_state)

def should_accept_signal(symbol, side):
    """
    server.py からENTRY_BUY/ENTRY_SELL受信時に呼ばれる。

    戻り値:
      (True, "")  ならエントリー許可
      (False, "理由") なら拒否してログに書く
    """
    orch_state = load_orch()

    # Top銘柄リストを一応整える
    refresh_top_symbols()

    # クールダウン中は入らない
    if is_cooldown(symbol, orch_state):
        return (False, "cooldown")

    # Top銘柄以外は入らない
    if symbol not in orch_state.get("active_symbols", []):
        return (False, "not_in_top")

    # ここで拡張：
    # 例えばボラが高すぎる/低すぎる、出来高が薄すぎる等はあとでここで弾く。
    # さらに「AI推奨ブレイクレンジ」(ai_selector側) と比較してもいい。
    #
    # 例:
    #   if not ai_selector.is_good_break(symbol, side):
    #       return (False, "ai_reject")
    #
    # 今はシンプルに許可。
    return (True, "")

def mark_symbol_active(symbol):
    """
    採用した銘柄をactive_symbolsの先頭に押し上げる。
    これで「いま注目してる銘柄ほど優先度高い」状態になる。
    """
    orch_state = load_orch()
    active_list = orch_state.get("active_symbols", [])
    if symbol in active_list:
        # 既にあるなら一旦消して先頭に入れ直す
        active_list.remove(symbol)
    active_list.insert(0, symbol)
    orch_state["active_symbols"] = active_list[:TOP_LIMIT]
    save_orch(orch_state)

def mark_symbol_closed(symbol):
    """
    ポジ手仕舞いした銘柄にクールダウンを入れる。
    """
    put_cooldown(symbol, minutes=5)
