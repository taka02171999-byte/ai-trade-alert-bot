# report_daily.py — 日次集計（損益・勝率・PF・銘柄別トップ3）をDiscordへ（JST）
import os, csv
from pathlib import Path
from datetime import datetime, timezone, timedelta
import requests

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
CSV_TRADES = Path("logs") / "trades.csv"

def jst_now():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9)))

def jst_day_range():
    now = jst_now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end   = day_start.replace(hour=23, minute=59, second=59)
    return day_start.isoformat(), day_end.isoformat(), day_start.strftime("%Y-%m-%d")

def post_embed(title, desc, color=0x1abc9c):
    if not DISCORD_WEBHOOK:
        print("[warn] DISCORD_WEBHOOK not set")
        return
    payload = {"embeds": [{
        "title": title,
        "description": desc,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "AIりんご式 日次レポート"}
    }]}
    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
        print("discord:", r.status_code)
    except Exception as e:
        print("discord error:", e)

def main():
    if not CSV_TRADES.exists():
        post_embed("📊 日次レポート", "本日のトレードはありませんでした。", 0x95a5a6)
        return

    start_iso, end_iso, label = jst_day_range()

    total = 0.0
    wins = 0; losses = 0; count = 0
    profit_sum = 0.0; loss_sum = 0.0
    per_symbol = {}

    with CSV_TRADES.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            close_ts = row.get("close_ts") or ""
            if not (start_iso <= close_ts <= end_iso):  # JST 当日クローズのみ
                continue
            count += 1
            try:
                pnl = float(row.get("pnl_pct") or 0.0)
            except:
                pnl = 0.0
            sym = (row.get("symbol") or "-").upper()

            total += pnl
            if pnl >= 0:
                wins += 1; profit_sum += pnl
            else:
                losses += 1; loss_sum += abs(pnl)

            per_symbol.setdefault(sym, 0.0)
            per_symbol[sym] += pnl

    if count == 0:
        post_embed(f"📊 {label} 日次レポート", "本日のトレードはありませんでした。", 0x95a5a6)
        return

    winrate = wins / count * 100.0
    pf = (profit_sum / loss_sum) if loss_sum > 0 else profit_sum
    top = sorted(per_symbol.items(), key=lambda x: -x[1])[:3]
    lines = [f"・{s}: {v:+.2f}%" for s, v in top] if top else ["（銘柄別集計なし）"]

    desc = (
        f"日付: **{label}** (JST)\n"
        f"総損益: **{total:+.2f}%**\n"
        f"勝率  : **{winrate:.1f}%** ({wins}/{count})\n"
        f"PF    : **{pf:.2f}**\n"
        f"上位: \n" + "\n".join(lines)
    )
    post_embed("📈 AIりんご式 日次レポート", desc, 0x1abc9c if total >= 0 else 0xe74c3c)

if __name__ == "__main__":
    main()
