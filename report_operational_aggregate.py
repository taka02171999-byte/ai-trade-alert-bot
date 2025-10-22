# report_operational_aggregate.py — 日次実績をDiscord投稿（選定通知ベース）
import os, pandas as pd, httpx, sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK","")
TRADES_CSV = Path("logs/trades.csv")
DB_PATH = Path("data/state.db")

def jst_day():
    return (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d")

def is_selected(symbol: str, ymd: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT 1 FROM selected WHERE symbol=? AND ymd=?", (symbol, ymd))
    ok = cur.fetchone() is not None
    conn.close()
    return ok

async def main():
    if not TRADES_CSV.exists(): return
    df = pd.read_csv(TRADES_CSV)
    if df.empty: return
    day = jst_day()
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    df["ymd"] = df["ts"].dt.tz_localize("UTC").dt.tz_convert("Asia/Tokyo").dt.strftime("%Y-%m-%d")

    # “その日の選定銘柄”だけ抽出（通知方針どおり）
    df_day = df[df["ymd"]==day]
    df_day = df_day[df_day["symbol"].apply(lambda s: is_selected(s, day))]
    if df_day.empty:
        msg = f"📅 日次レポート（{day}）\n選定銘柄の実取引なし"
    else:
        df_day["score"] = df_day["outcome"].map({"TP":1,"SL":-1}).fillna(0)
        win = (df_day["outcome"]=="TP").mean()*100
        total = df_day["score"].sum()
        lines = [f"- {r.symbol}: {r.outcome} / {r.agent} (entry={r.entry}, exit={r.exit})"
                 for r in df_day.itertuples()]
        msg = f"📅 日次レポート（{day}）\n勝率: {win:.1f}% / スコア合計: {total:.1f}\n" + "\n".join(lines[:40])

    if DISCORD_WEBHOOK:
        async with httpx.AsyncClient(timeout=10) as cli:
            await cli.post(DISCORD_WEBHOOK, json={"content": msg})

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
