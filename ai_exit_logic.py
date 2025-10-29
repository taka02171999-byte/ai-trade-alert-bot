# ai_exit_logic.py
#
# AI側の意思決定フック
# - shadow_pendingをrealに昇格させるべきか？
# - そろそろ手仕舞い(利確/損切り/タイムアウト)すべきか？

def _get_last_tick(pos):
    ticks = pos.get("ticks", [])
    return ticks[-1] if ticks else None

def should_promote_to_real(position_dict):
    """
    shadow_pending → real へ“後追いエントリー”していいか？
    ざっくりの初期ルール：
    - BUYならエントリー価格から順行して+0.4%以上進んでる
    - SELLも同じで、pctがプラス=有利進行っていうPine側仕様を前提に共通で扱う
    """
    if not position_dict:
        return False

    if position_dict.get("status") != "shadow_pending":
        return False

    last_tick = _get_last_tick(position_dict)
    if not last_tick:
        return False

    pct_now = float(last_tick.get("pct", 0.0))

    # “しっかり走り始めた”とみなす最低ライン
    return pct_now >= 0.4

def should_exit_now(position_dict):
    """
    AIがこのポジを今ここでクローズしたい？
    ざっくりの初期ルール：
    - 含み益が+1.0%以上 → AI利確 (AI_TP)
    - 含み損が-0.6%以下 → AI損切り (AI_SL)
    - mins_from_entryが15分以上 → AIタイムアウト (AI_TIMEOUT)
      ※Pine側もタイムアウト30分やEODで飛ばしてくるけど、それより早く降りる判断ができる

    戻り値:
      wants_exit(bool), (exit_type:str, exit_price:float) or None
    """
    if (not position_dict) or position_dict.get("closed"):
        return False, None

    last_tick = _get_last_tick(position_dict)
    if not last_tick:
        return False, None

    pct_now = float(last_tick.get("pct", 0.0))
    price_now = float(last_tick.get("price", 0.0))

    # Pine側から渡される「実質ホールド時間(分)」
    mins_from_entry = last_tick.get("mins_from_entry")
    try:
        mins_from_entry = float(mins_from_entry) if mins_from_entry is not None else None
    except Exception:
        mins_from_entry = None

    # 利確
    if pct_now >= 1.0:
        return True, ("AI_TP", price_now)

    # 損切り
    if pct_now <= -0.6:
        return True, ("AI_SL", price_now)

    # タイムアウト（AI側の方がタイトめに15分で降りる）
    if mins_from_entry is not None and mins_from_entry >= 15:
        return True, ("AI_TIMEOUT", price_now)

    return False, None
# ai_exit_logic.py
#
# AI側の意思決定フック
# - shadow_pendingをrealに昇格させるべきか？
# - そろそろ手仕舞い(利確/損切り/タイムアウト)すべきか？

def _get_last_tick(pos):
    ticks = pos.get("ticks", [])
    return ticks[-1] if ticks else None

def should_promote_to_real(position_dict):
    """
    shadow_pending → real へ“後追いエントリー”していいか？
    ざっくりの初期ルール：
    - BUYならエントリー価格から順行して+0.4%以上進んでる
    - SELLも同じで、pctがプラス=有利進行っていうPine側仕様を前提に共通で扱う
    """
    if not position_dict:
        return False

    if position_dict.get("status") != "shadow_pending":
        return False

    last_tick = _get_last_tick(position_dict)
    if not last_tick:
        return False

    pct_now = float(last_tick.get("pct", 0.0))

    # “しっかり走り始めた”とみなす最低ライン
    return pct_now >= 0.4

def should_exit_now(position_dict):
    """
    AIがこのポジを今ここでクローズしたい？
    ざっくりの初期ルール：
    - 含み益が+1.0%以上 → AI利確 (AI_TP)
    - 含み損が-0.6%以下 → AI損切り (AI_SL)
    - mins_from_entryが15分以上 → AIタイムアウト (AI_TIMEOUT)
      ※Pine側もタイムアウト30分やEODで飛ばしてくるけど、それより早く降りる判断ができる

    戻り値:
      wants_exit(bool), (exit_type:str, exit_price:float) or None
    """
    if (not position_dict) or position_dict.get("closed"):
        return False, None

    last_tick = _get_last_tick(position_dict)
    if not last_tick:
        return False, None

    pct_now = float(last_tick.get("pct", 0.0))
    price_now = float(last_tick.get("price", 0.0))

    # Pine側から渡される「実質ホールド時間(分)」
    mins_from_entry = last_tick.get("mins_from_entry")
    try:
        mins_from_entry = float(mins_from_entry) if mins_from_entry is not None else None
    except Exception:
        mins_from_entry = None

    # 利確
    if pct_now >= 1.0:
        return True, ("AI_TP", price_now)

    # 損切り
    if pct_now <= -0.6:
        return True, ("AI_SL", price_now)

    # タイムアウト（AI側の方がタイトめに15分で降りる）
    if mins_from_entry is not None and mins_from_entry >= 15:
        return True, ("AI_TIMEOUT", price_now)

    return False, None
