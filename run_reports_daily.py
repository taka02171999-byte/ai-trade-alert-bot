# run_reports_daily.py
# ==========================================
# 毎営業日用（日次レポ）
# - 土日祝は送らない
# - AI学習などは一切しない
# ==========================================

import os
from datetime import datetime, timedelta, date
import pytz

from report_daily import generate_daily_report
from utils.discord import send_discord


# --------------------
# 日本の祝日判定（外部ライブラリ無し）
# --------------------
def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    # weekday: Mon=0 ... Sun=6
    d = date(year, month, 1)
    # 1日に近いweekdayまで進める
    while d.weekday() != weekday:
        d = d + timedelta(days=1)
    # n回目へ
    return d + timedelta(days=7 * (n - 1))

def _vernal_equinox_day(year: int) -> int:
    # 近似式（1980-2099程度で実用）
    return int(20.8431 + 0.242194 * (year - 1980) - int((year - 1980) / 4))

def _autumnal_equinox_day(year: int) -> int:
    return int(23.2488 + 0.242194 * (year - 1980) - int((year - 1980) / 4))

def jp_holidays(year: int) -> set:
    h = set()

    # 固定日
    h.add(date(year, 1, 1))   # 元日
    h.add(date(year, 2, 11))  # 建国記念の日
    h.add(date(year, 2, 23))  # 天皇誕生日
    h.add(date(year, 4, 29))  # 昭和の日
    h.add(date(year, 5, 3))   # 憲法記念日
    h.add(date(year, 5, 4))   # みどりの日
    h.add(date(year, 5, 5))   # こどもの日
    h.add(date(year, 8, 11))  # 山の日
    h.add(date(year, 11, 3))  # 文化の日
    h.add(date(year, 11, 23)) # 勤労感謝の日

    # ハッピーマンデー
    h.add(_nth_weekday(year, 1, 0, 2))   # 成人の日: 1月第2月曜
    h.add(_nth_weekday(year, 7, 0, 3))   # 海の日: 7月第3月曜
    h.add(_nth_weekday(year, 9, 0, 3))   # 敬老の日: 9月第3月曜
    h.add(_nth_weekday(year, 10, 0, 2))  # スポーツの日: 10月第2月曜

    # 春分・秋分
    h.add(date(year, 3, _vernal_equinox_day(year)))
    h.add(date(year, 9, _autumnal_equinox_day(year)))

    # 振替休日（簡易：日曜に当たる祝日の翌日）
    subs = set()
    for d in h:
        if d.weekday() == 6:  # Sunday
            subs.add(d + timedelta(days=1))
    h |= subs

    # 国民の休日（祝日と祝日に挟まれた平日）
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

    # 土日祝スキップ
    if not is_business_day_jp(today):
        print(f"[run_reports_daily] skip: not business day ({today})")
        return

    hook = os.getenv("DISCORD_WEBHOOK_REPORT", "")
    msg = generate_daily_report()

    if hook:
        send_discord(hook, msg)
    else:
        print("[run_reports_daily] ⚠ DISCORD_WEBHOOK_REPORT 未設定")
        print(msg)


if __name__ == "__main__":
    main()
