# optimizer_daily.py
import json
from pathlib import Path
import pandas as pd
import yfinance as yf
from orchestrator import to_yf_symbol, load_universe, today_str

DATA_DIR = Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
TOP10_FILE = DATA_DIR / "top10.json"

def main():
    uni = load_universe()
    if not uni:
        TOP10_FILE.write_text(json.dumps({"date": today_str(), "symbols": []}, ensure_ascii=False), encoding="utf-8")
        return

    rows = []
    for s in uni:
        try:
            yfs = to_yf_symbol(s)
            df = yf.download(yfs, period="5d", interval="1m", progress=False, auto_adjust=True, threads=False)
            if df is None or df.empty: 
                continue
            rng = (df["High"] - df["Low"]).abs().mean()
            rows.append((s, float(rng)))
        except Exception:
            continue

    rows.sort(key=lambda x: x[1], reverse=True)
    top10 = [r[0] for r in rows[:10]] if rows else uni[:10]
    TOP10_FILE.write_text(json.dumps({"date": today_str(), "symbols": top10}, ensure_ascii=False), encoding="utf-8")

if __name__ == "__main__":
    main()
