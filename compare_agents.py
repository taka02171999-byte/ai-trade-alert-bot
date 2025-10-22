# compare_agents.py — 直近ログで 'fixed' or 'rt' を銘柄別に選択
import os, sqlite3, pandas as pd
from pathlib import Path
from datetime import datetime

DATA_DIR = Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "state.db"
TRADES_CSV = Path("logs/trades.csv")

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS best_agents(
        symbol TEXT PRIMARY KEY,
        agent TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    return conn

def pick_agent(df_sym):
    if df_sym.empty: return "fixed"
    last = df_sym.tail(60)
    win_fixed = ((last["agent"]=="fixed") & (last["outcome"]=="TP")).sum()
    n_fixed   = (last["agent"]=="fixed").sum() or 1
    win_rt    = ((last["agent"]=="rt") & (last["outcome"]=="TP")).sum()
    n_rt      = (last["agent"]=="rt").sum() or 1
    return "rt" if (win_rt/n_rt) > (win_fixed/n_fixed) else "fixed"

def main():
    if not TRADES_CSV.exists(): return
    df = pd.read_csv(TRADES_CSV)
    if df.empty: return
    with _db() as conn:
        for sym, g in df.groupby("symbol"):
            agent = pick_agent(g)
            conn.execute("REPLACE INTO best_agents(symbol,agent,updated_at) VALUES(?,?,?)",
                         (sym, agent, datetime.utcnow().isoformat()))
        conn.commit()

if __name__ == "__main__":
    main()
