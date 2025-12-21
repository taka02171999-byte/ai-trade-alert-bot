from datetime import datetime, timezone, timedelta
import jpholiday

JST = timezone(timedelta(hours=9))

def jst_now() -> datetime:
    return datetime.now(JST)

def get_jst_now_str() -> str:
    return jst_now().strftime("%Y/%m/%d %H:%M:%S")

def is_weekend(dt: datetime) -> bool:
    # 5=土 6=日
    return dt.weekday() >= 5

def is_jp_holiday(dt: datetime) -> bool:
    # jpholidayはdateを渡す
    return jpholiday.is_holiday(dt.date())

def is_business_day(dt: datetime) -> bool:
    return (not is_weekend(dt)) and (not is_jp_holiday(dt))

def session_from_time(dt: datetime) -> str:
    """
    payloadにsessionが無い場合の推定。
    09:00-11:30 => AM
    12:30-15:30 => PM
    その他 => OTHER
    """
    hhmm = dt.strftime("%H:%M")
    if "09:00" <= hhmm <= "11:30":
        return "AM"
    if "12:30" <= hhmm <= "15:30":
        return "PM"
    return "OTHER"
