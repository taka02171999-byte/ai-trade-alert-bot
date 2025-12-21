# run_reports_monthly.py
# ==========================================
# 月次レポ（Cronで毎月1日などに叩く想定）
# - 土日祝は送らない
# ==========================================

import os
from datetime import datetime, timedelta, date
import pytz

from report_monthly import generate_monthly_report
from utils.discord import send_discord


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != weekday:
        d = d + timedelta(days=1)
    return d + timedelta(days=7 * (n - 1))

def _vernal_equinox_day(year: int) -> int:
    return int(20.8431 + 0.242194 * (year - 1980) - int((year - 1980) / 4))

def _autumnal_equinox_day(year: int) -> int:
    return int(23.2488 + 0.242194 * (year - 1980) - int((year - 1980) / 4))

def jp_holidays(year: int) -> set:
    h = set()
    h.add(date(year, 1, 1))
    h.add(date(year, 2, 11))
    h.add(date(year, 2, 23))
    h.add(date(year, 4, 29))
    h.add(date(year, 5, 3))
    h.add(date(year, 5, 4))
    h.add(date(year, 5, 5))
    h.add(date(year, 8, 11))
    h.add(date(year, 11, 3))
    h.add(date(year, 11, 23))

    h.add(_nth_weekday(year, 1, 0, 2))
    h.add(_nth_weekday(year, 7, 0, 3))
    h.add(_nth_weekday(year, 9, 0, 3))
    h.add(_nth_weekday(year, 10, 0, 2))

    h.add(date(year, 3, _vernal_equinox_day(year)))
    h.add(date(year, 9, _autumnal_equinox_day(year)))

    subs = set()
    for d in h:
        if d.weekday() == 6:
            subs.add(d + timedelta(days=1))
    h |= subs

    for m in range(1, 13):
        for day in range(1, 32):
            try:
                d = date(year, m, day)
            except ValueError:
                continue
            if d in h:
                continue
            if (d - timedelta(days=1)) in h and (d + timedelta(days=1)) in h:
                h.add(d)

    return h

def is_business_day_jp(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    if d in jp_holidays(d.year):
        return False
    return True


def main():
    JST = pytz.timezone("Asia/Tokyo")
    now_jst = datetime.now(JST)
    today = now_jst.date()

    if not is_business_day_jp(today):
        print(f"[run_reports_monthly] skip: not business day ({today})")
        return

    hook = os.getenv("DISCORD_WEBHOOK_REPORT", "")
    msg = generate_monthly_report()

    if hook:
        send_discord(hook, msg)
    else:
        print("[run_reports_monthly] ⚠ DISCORD_WEBHOOK_REPORT 未設定")
        print(msg)


if __name__ == "__main__":
    main()
