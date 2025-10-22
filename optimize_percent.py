# optimize_percent.py — TP/SL％と追跡時間(初動/TP)を銘柄ごとに日次最適化
import os, sqlite3
from pathlib import Path
from datetime import datetime
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()
DATA_DIR = Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "state.db"
LOOKBACK = int(os.getenv("OPTIMIZE_LOOKBACK_DAYS", "14"))
FOLLOW_DEFAULT = int(os.getenv("FOLLOW_MIN_DEFAULT", "3"))
TRACK_DEFAULT  = int(os.getenv("TP_SL_TRACK_MIN", "30"))

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS params(
        symbol TEXT PRIMARY KEY,
        tp_pct REAL NOT NULL,
        sl_pct REAL NOT NULL,
        follow_min INTEGER NOT NULL,
        track_min INTEGER NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS selected(
        symbol TEXT NOT NULL,
        ymd TEXT NOT NULL,
        PRIMARY KEY(symbol, ymd)
    )""")
    return conn

def _optimize_symbol(sym):
    # 日足ベースのラフ最適化：ATR%と直近勝率（ログがあれば）で調整
    df = yf.download(sym, period="3mo", interval="1d", progress=False)
    if df.empty or len(df) < LOOKBACK: return None
    sub = df.tail(LOOKBACK)
    atr = (sub["High"]-sub["Low"]).rolling(14).mean().iloc[-1]
    close = sub["Close"].iloc[-1]
    atr_pct = float(atr/close) if close else 0.01

    # ベース
    tp = max(0.004, min(0.02, atr_pct * 1.8))   # 0.4%〜2.0%
    sl = max(0.003, min(0.015, atr_pct * 1.1))  # 0.3%〜1.5%

    # 追跡時間はATR%からラフ調整（高ボラ→短め、低ボラ→長め）
    follow = max(2, min(6, int(round(FOLLOW_DEFAULT + (0.10 - atr_pct*4)))))   # だいたい2〜6分
    track  = max(15, min(60, int(round(TRACK_DEFAULT  + (0.10 - atr_pct*3)*60)))) # 15〜60分

    # 直近ログの勝率で微調整（あれば）
    log_path = Path("logs/trades.csv")
    if log_path.exists():
        logs = pd.read_csv(log_path)
        sym_logs = logs[logs["symbol"]==sym].tail(50)
        if not sym_logs.empty:
            win = (sym_logs["outcome"]=="TP").mean()
            # 勝率高→TP少し引き上げ/追跡やや長め、低→逆
            tp *= (1.0 + (win-0.5)*0.4)
            sl *= (1.0 - (win-0.5)*0.3)
            track = int(max(15, min(60, track + (win-0.5)*20)))

    return round(tp,6), round(sl,6), int(follow), int(track)

def main():
    # 直近選定＋ログに出てくる銘柄を対象に最適化
    syms = set()
    with _db() as conn:
        cur = conn.execute("SELECT DISTINCT symbol FROM selected")
        syms |= {r[0] for r in cur.fetchall()}
    log_path = Path("logs/trades.csv")
    if log_path.exists():
        df = pd.read_csv(log_path)
        syms |= set(df["symbol"].unique().tolist())
    if not syms: return

    with _db() as conn:
        for s in syms:
            opt = _optimize_symbol(s)
            if not opt: continue
            tp, sl, follow, track = opt
            conn.execute("""REPLACE INTO params(symbol,tp_pct,sl_pct,follow_min,track_min,updated_at)
                            VALUES(?,?,?,?,?,?)""",
                         (s, tp, sl, follow, track, datetime.utcnow().isoformat()))
        conn.commit()

if __name__ == "__main__":
    main()
