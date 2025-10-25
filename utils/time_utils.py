from datetime import datetime
import pytz

JST = pytz.timezone("Asia/Tokyo")

def get_jst_now():
    return datetime.now(JST)

def get_jst_now_str():
    return get_jst_now().isoformat(timespec="seconds")

def is_market_closed_now_jst(market_close_hhmm: str) -> bool:
    now = get_jst_now()
    try:
        hh, mm = map(int, market_close_hhmm.split(":"))
    except:
        hh, mm = 15, 25
    cutoff = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return now >= cutoff
