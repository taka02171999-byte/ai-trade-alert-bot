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
    if not os.path.exists(ORCH_STATE_PATH):
        return {
            "active_symbols": [],  # 今監視・採用OKな銘柄リスト
            "cooldown": {}         # { "7203.T": "2025-10-25T10:00:00" }
        }
    with open(ORCH_STATE_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except:
            return {
                "active_symbols": [],
                "cooldown": {}
            }

def save_orch(state):
    with open(ORCH_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def is_cooldown(symbol, orch_state):
    cd = orch_state.get("cooldown", {})
    if symbol not in cd:
        return False
    until_str = cd[symbol]
    try:
        until_dt = datetime.fromisoformat(until_str)
    except:
        return False
    return _utc_now() < until_dt

def put_cooldown(symbol, minutes=5):
    orch_state = load_orch()
    orch_state.setdefault("cooldown", {})
    orch_state["cooldown"][symbol] = (_utc_now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    save_orch(orch_state)

def refresh_top_symbols():
    """
    本番では:
      - 勝率とか
      - 流動性
      - その日のヒット率
    などからランキングして上位TOP_LIMITだけ残す。
    今は単純に今のactive_symbolsを先頭からTOP_LIMITに切り詰めるだけ。
    """
    orch_state = load_orch()
    active = orch_state.get("active_symbols", [])
    orch_state["active_symbols"] = active[:TOP_LIMIT]
    save_orch(orch_state)

def should_accept_signal(symbol, side):
    """
    server.py から呼ばれる。
    戻り値は4つ:
      accept(bool),
      reject_reason(str),
      tp_target(float|None),
      sl_target(float|None)

    tp_target / sl_target は Discord のエントリー通知にそのまま出す。
    今はダミーで None にしてあるので、あとでロジックを差し込めばOK。
    """
    orch_state = load_orch()
    refresh_top_symbols()

    # クールダウン中は拒否
    if is_cooldown(symbol, orch_state):
        return False, "cooldown", None, None

    # Top監視リスト外は拒否
    if symbol not in orch_state.get("active_symbols", []):
        return False, "not_in_top", None, None

    # --- ここが将来の"AIが決めた利確/損切り目安"
    # 例: tp_target = entry_price * 1.012 みたいなやつを計算して返す想定。
    # 今はまだentry_priceわからないので None にしておく。
    tp_target = None
    sl_target = None

    return True, "", tp_target, sl_target

def mark_symbol_active(symbol):
    orch_state = load_orch()
    if symbol not in orch_state["active_symbols"]:
        # 先頭に入れて優先度を上げるイメージ
        orch_state["active_symbols"].insert(0, symbol)
    save_orch(orch_state)

def mark_symbol_closed(symbol):
    # クローズ後はクールダウン入れる
    put_cooldown(symbol, minutes=5)
