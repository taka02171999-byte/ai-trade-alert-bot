from datetime import datetime
import pytz

JST = pytz.timezone("Asia/Tokyo")

def get_jst_now():
    return datetime.now(JST)

def get_jst_now_str():
    # "2025-10-26T09:41:00+09:00" みたいなISO風
    return get_jst_now().isoformat(timespec="seconds")

def is_market_closed_now_jst(market_close_hhmm: str) -> bool:
    """
    market_close_hhmm: "15:25" みたいな文字列
    現在JST時刻がそれ以降なら True
    """
    now = get_jst_now()
    try:
        hh, mm = market_close_hhmm.split(":")
        close_h = int(hh)
        close_m = int(mm)
    except:
        close_h = 15
        close_m = 25

    # すでに引け時刻を過ぎているか
    if now.hour > close_h:
        return True
    if now.hour == close_h and now.minute >= close_m:
        return True
    return False
