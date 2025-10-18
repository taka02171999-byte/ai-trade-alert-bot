# optimize_percent.py
import os, json, requests, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

# ====== 環境変数 ======
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
SYMBOLS = os.getenv("SYMBOLS", "8136.T").split(",")  # 例: "8136.T,7203.T,BTC-USD"
TIMEFRAME = os.getenv("TIMEFRAME", "5m")
DAYS = int(os.getenv("DAYS", "30"))
SESS = os.getenv("SESS", "09:00-11:30,12:30-15:00")    # JSTセッション
MIN_ATR_PCT = float(os.getenv("MIN_ATR_PCT", "0.10"))  # ATR% 最低
PENALTY = float(os.getenv("PENALTY", "0.0"))           # 往復コスト(pt) 任意

# グリッド（必要なら増やす）
BREAKS_PCT = [0.05, 0.10, 0.20, 0.30, 0.50]  # 0.10 = 0.1%
VOLMULTS   = [1.0, 1.2, 1.5, 1.8]
SLATRS     = [0.8, 1.0, 1.2]
TPATRS     = [1.6, 1.8, 2.0]
COOLS      = [8, 10, 12]

# 保存先
BASE = Path(__file__).parent
PARAMS_FILE = BASE / "params.json"

JST = timezone(timedelta(hours=9))
def jstnow():
    return datetime.now(timezone.utc).astimezone(JST)

def post_discord(title, desc, fields=None, color=0x3498db):
    if not DISCORD_WEBHOOK:
        print("no DISCORD_WEBHOOK")
        return
    payload = {
        "embeds": [{
            "title": title,
            "description": desc,
            "color": color,
            "fields": fields or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "AIりんご式 | 最適化レポート"}
        }]
    }
    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
        print("discord:", r.status_code)
    except Exception as e:
        print("discord error:", e)

def load_params():
    if PARAMS_FILE.exists():
        try:
            return json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
        except:
            return {}
    return {}

def save_params(d):
    PARAMS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

def fetch_df(symbol: str, tf: str, days: int) -> pd.DataFrame:
    period = f"{days}d"
    df = yf.download(symbol.strip(), period=period, interval=tf, auto_adjust=False, progress=False)
    if df.empty:
        return df
    df = df.rename(columns=str.lower)
    # timezone
    df.index = df.index.tz_convert("Asia/Tokyo") if df.index.tz is not None else df.index.tz_localize("Asia/Tokyo")
    # 指標
    df["vwap"] = (df["close"]*df["volume"]).rolling(20, min_periods=1).sum() / df["volume"].rolling(20, min_periods=1).sum()
    df["vol20"] = df["volume"].rolling(20).mean()
    # ATR
    tr = pd.concat([
        (df["high"] - df["low"]).abs(),
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"]  - df["close"].shift(1)).abs()
    ], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
    return df.dropna()

def filter_session(df: pd.DataFrame, sess: str) -> pd.DataFrame:
    keep = pd.Series(False, index=df.index)
    for part in sess.split(","):
        part = part.strip()
        if not part: continue
        s,e = part.split("-")
        t1 = pd.to_datetime(s).time()
        t2 = pd.to_datetime(e).time()
        keep |= (df.index.time >= t1) & (df.index.time <= t2)
    return df.loc[keep]

def backtest(df: pd.DataFrame, br_pct, vm, slm, tpm, cool):
    # ％変化、方向、出来高
    pct = (df["close"]/df["close"].shift(1) - 1.0) * 100.0
    prevBull = (df["close"].shift(1) > df["open"].shift(1)) & (df["close"].shift(1) > df["vwap"].shift(1))
    prevBear = (df["close"].shift(1) < df["open"].shift(1)) & (df["close"].shift(1) < df["vwap"].shift(1))
    volOK = df["volume"] > df["vol20"] * vm
    trendUp = (df["close"] > df["ema200"]) & (df["ema200"] > df["ema200"].shift(1))
    trendDn = (df["close"] < df["ema200"]) & (df["ema200"] < df["ema200"].shift(1))
    atrPct  = df["atr14"] / df["close"] * 100.0

    buySig  = (pct >= br_pct) & prevBull & volOK & trendUp & (atrPct >= MIN_ATR_PCT)
    sellSig = (pct <= -br_pct) & prevBear & volOK & trendDn & (atrPct >= MIN_ATR_PCT)

    pos = 0
    entry = np.nan
    cool_now = 0
    wins = 0
    losses = 0
    trades = 0
    pnl_sum = 0.0

    for i in range(1, len(df)):
        c = df["close"].iloc[i]
        h = df["high"].iloc[i]
        l = df["low"].iloc[i]
        atr = df["atr14"].iloc[i]

        # クールダウン
        if cool_now > 0:
            cool_now -= 1

        # 決済判定（ロング）
        if pos == 1:
            stop = entry - atr * slm
            take = entry + atr * tpm
            pl = None
            if l <= stop:
                pl = stop - entry
            elif h >= take:
                pl = take - entry
            if pl is not None:
                pl -= PENALTY
                pnl_sum += pl
                trades += 1
                if pl >= 0: wins += 1
                else: losses += 1
                pos = 0
                entry = np.nan
                cool_now = cool

        # 決済判定（ショート）
        elif pos == -1:
            stop = entry + atr * slm
            take = entry - atr * tpm
            pl = None
            if h >= stop:
                pl = entry - stop
            elif l <= take:
                pl = entry - take
            if pl is not None:
                pl -= PENALTY
                pnl_sum += pl
                trades += 1
                if pl >= 0: wins += 1
                else: losses += 1
                pos = 0
                entry = np.nan
                cool_now = cool

        # エントリー
        if pos == 0 and cool_now == 0:
            if buySig.iloc[i]:
                pos = 1
                entry = c
            elif sellSig.iloc[i]:
                pos = -1
                entry = c

    # PF（総利益/総損失）
    # 簡易のため勝ち負け件数比をPF近似に利用（必要なら明細保持に変更可）
    pf = wins / max(losses, 1)
    winpct = (wins / max(trades, 1)) * 100.0

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "pnl": round(pnl_sum, 2),
        "pf": round(pf, 2),
        "winpct": round(winpct, 2)
    }

def optimize_one(symbol: str):
    df = fetch_df(symbol, TIMEFRAME, DAYS)
    if df.empty:
        post_discord("❗最適化エラー", f"{symbol}: データが空でした", color=0xE74C3C)
        return None

    df = filter_session(df, SESS)
    if len(df) < 200:
        post_discord("ℹ️ データ不足", f"{symbol}: バー数が少ないため結果が安定しない可能性", color=0x3498db)

    results = []
    for br in BREAKS_PCT:
        for vm in VOLMULTS:
            for sl in SLATRS:
                for tp in TPATRS:
                    for cd in COOLS:
                        m = backtest(df, br, vm, sl, tp, cd)
                        score = (m["pf"], m["pnl"], m["winpct"], m["trades"])
                        results.append({"symbol":symbol, "break_pct":br, "vol":vm, "sl":sl, "tp":tp, "cool":cd, **m, "score":score})

    results.sort(key=lambda r: r["score"], reverse=True)
    top5 = results[:5]
    best = top5[0]

    # params.json 更新（現状は SL/TP を使用）
    params = load_params()
    params[symbol] = {"sl_atr": best["sl"], "tp_atr": best["tp"]}
    save_params(params)

    # Discord レポート
    lines = []
    lines.append(f"**{symbol} / {TIMEFRAME} / {DAYS}d** の最適化（％ブレイク）")
    lines.append("")
    lines.append("**Best**")
    lines.append(f"- br={best['break_pct']:.2f}%  vol×{best['vol']:.1f}  SL×{best['sl']:.1f}  TP×{best['tp']:.1f}  cool={best['cool']}")
    lines.append(f"- PF={best['pf']:.2f}  Win={best['winpct']:.1f}%  Tr={best['trades']}  PnL={best['pnl']:.2f}")
    lines.append("")
    lines.append("**Top5**")
    for i, r in enumerate(top5, 1):
        lines.append(f"{i}. br={r['break_pct']:.2f}% vol×{r['vol']:.1f} SL×{r['sl']:.1f} TP×{r['tp']:.1f} cool={r['cool']} | PF={r['pf']:.2f} Win={r['winpct']:.1f}% Tr={r['trades']} P={r['pnl']:.2f}")

    post_discord("🤖 夜間最適化レポート（％）", "```\n" + "\n".join(lines) + "\n```", color=0x2ecc71)
    return best

def main():
    try:
        for sym in SYMBOLS:
            optimize_one(sym.strip())
    except Exception as e:
        post_discord("❗最適化例外", f"```{e}\n{traceback.format_exc()}```", color=0xE74C3C)

if __name__ == "__main__":
    main()
