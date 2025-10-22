# select_symbols.py — 出来高×ボラで上位選出→DB保存→Discordに今日の監視銘柄を投稿
import os, sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd
import yfinance as yf
import httpx
from dotenv import load_dotenv

load_dotenv()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
SELECT_TOP_N = int(os.getenv("SELECT_TOP_N", "40"))

DATA_DIR = Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "state.db"
UNIVERSE_TXT = DATA_DIR / "universe.txt"

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS selected(
        symbol TEXT NOT NULL,
        ymd TEXT NOT NULL,
        PRIMARY KEY(symbol, ymd)
    )""")
    return conn

def jst_ymd():
    return (datetime.utcnow() + timedelta(hours=9)).strftime("%Y-%m-%d")

def load_universe():
    if UNIVERSE_TXT.exists():
        return [s.strip() for s in UNIVERSE_TXT.read_text().splitlines() if s.strip()]
    return ["7203.T","6758.T","9984.T","9432.T","7974.T","6954.T","8306.T","4502.T","6981.T","9101.T"]

def rank_symbols(symbols):
    rows=[]
    for s in symbols:
        try:
            df = yf.download(s, period="1mo", interval="1d", progress=False)
            if df.empty: continue
            atr = (df["High"]-df["Low"]).rolling(14).mean().iloc[-1]
            vol = df["Volume"].rolling(20).mean().iloc[-1]
            close = df["Close"].iloc[-1]
            if pd.isna(atr) or pd.isna(vol) or pd.isna(close): continue
            atr_pct = (atr/close)*100.0
            score = atr_pct*0.7 + (vol/1e6)*0.3
            rows.append((s, score))
        except Exception: pass
    df = pd.DataFrame(rows, columns=["symbol","score"]).sort_values("score", ascending=False)
    return df.head(SELECT_TOP_N)

async def main():
    syms = load_universe()
    df = rank_symbols(syms)
    ymd = jst_ymd()
    with _db() as conn:
        conn.execute("DELETE FROM selected WHERE ymd=?", (ymd,))
        conn.executemany("REPLACE INTO selected(symbol, ymd) VALUES(?,?)", [(s, ymd) for s in df["symbol"]])
        conn.commit()
    if DISCORD_WEBHOOK:
        msg = "🧮 今日の“選定銘柄” (Top {}):\n{}".format(
            SELECT_TOP_N, "\n".join(f"- {s}" for s in df["symbol"].tolist()))
        async with httpx.AsyncClient(timeout=10) as cli:
            await cli.post(DISCORD_WEBHOOK, json={"content": msg})

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
