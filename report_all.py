# report_all.py — 週次・月次のまとめ（選定銘柄の実績ベース）
import os, pandas as pd, httpx, sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK","")
TRADES_CSV = Path("logs/trades.csv")
DB_PATH = Path("data/state.db")

def is_selected_day(symbol: str, ymd: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("SELECT 1 FROM selected WHERE symbol=? AND ymd=?", (symbol, ymd))
    ok = cur.fetchone() is not None
    conn.close()
    return ok

def period_df(days: int):
    if not TRADES_CSV.exists(): return None
    df = pd.read_csv(TRADES_CSV)
    if df.empty: return None
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    since = datetime.utcnow() - timedelta(days=days)
    df = df[df["ts"] >= since]
    df["ymd"] = df["ts"].dt.tz_localize("UTC").dt.tz_convert("Asia/Tokyo").dt.strftime("%Y-%m-%d")
    # 選定日の実績のみ
    return df[df.apply(lambda r: is_selected_day(r["symbol"], r["ymd"]), axis=1)]

def summarize(df, title):
    if df is None or df.empty:
        return f"{title}\n取引なし"
    win = (df["outcome"]=="TP").mean()*100
    score = df["outcome"].map({"TP":1,"SL":-1}).fillna(0).sum()
    n = len(df)
    return f"{title}\n勝率: {win:.1f}% / スコア合計: {score:.1f} / 取引数: {n}"

async def main():
    weekly = summarize(period_df(7),  "📈 週次レポート")
    monthly= summarize(period_df(30), "📊 月次レポート")
    msg = weekly + "\n" + monthly
    if DISCORD_WEBHOOK:
        async with httpx.AsyncClient(timeout=10) as cli:
            await cli.post(DISCORD_WEBHOOK, json={"content": msg})

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
