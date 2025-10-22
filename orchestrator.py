# orchestrator.py — 選定のみ通知・非選定は裏学習／初動→本エントリー化／TPSL追跡
import os, time, sqlite3, json
from pathlib import Path
from datetime import datetime, timezone, timedelta

import httpx
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "").strip()
DEFAULT_TP_PCT = float(os.getenv("DEFAULT_TP_PCT", "0.8")) / 100.0
DEFAULT_SL_PCT = float(os.getenv("DEFAULT_SL_PCT", "0.5")) / 100.0

# 時間系
FOLLOW_MIN_DEFAULT = int(os.getenv("FOLLOW_MIN_DEFAULT", "3"))      # 初動フォロー
TP_SL_TRACK_MIN    = int(os.getenv("TP_SL_TRACK_MIN", "30"))        # 本追跡
CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "45"))
ENTRY_ATR_MUL      = float(os.getenv("ENTRY_ATR_MUL", "0.30"))

# 通知/学習ポリシー
SELECT_TOP_N        = int(os.getenv("SELECT_TOP_N", "40"))
USE_SELECTION_ONLY  = os.getenv("USE_SELECTION_ONLY", "true").lower() == "true"
LEARN_FROM_ALL      = os.getenv("LEARN_FROM_ALL", "true").lower() == "true"

DATA_DIR  = Path("data");  DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR  = Path("logs");  LOGS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH   = DATA_DIR / "state.db"
TRADES_CSV= LOGS_DIR / "trades.csv"

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
    conn.execute("""CREATE TABLE IF NOT EXISTS best_agents(
        symbol TEXT PRIMARY KEY,
        agent TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    return conn

async def _discord(text: str):
    if not DISCORD_WEBHOOK: return
    async with httpx.AsyncClient(timeout=10) as cli:
        await cli.post(DISCORD_WEBHOOK, json={"content": text})

def _today_jst_ymd():
    jst = timezone(timedelta(hours=9))
    return datetime.now(jst).strftime("%Y-%m-%d")

def is_selected_today(symbol: str) -> bool:
    ymd = _today_jst_ymd()
    with _db() as conn:
        cur = conn.execute("SELECT 1 FROM selected WHERE symbol=? AND ymd=?", (symbol, ymd))
        return cur.fetchone() is not None

def get_params(symbol: str):
    with _db() as conn:
        cur = conn.execute("SELECT tp_pct, sl_pct, follow_min, track_min FROM params WHERE symbol=?", (symbol,))
        row = cur.fetchone()
    if row:
        return float(row[0]), float(row[1]), int(row[2]), int(row[3])
    # デフォルト（学習前）
    return DEFAULT_TP_PCT, DEFAULT_SL_PCT, FOLLOW_MIN_DEFAULT, TP_SL_TRACK_MIN

def _append_trade(row: dict):
    exists = TRADES_CSV.exists()
    df = pd.DataFrame([row])
    df.to_csv(TRADES_CSV, mode="a", header=not exists, index=False)

def _fetch(symbol: str, interval="1m", lookback_min=60):
    try:
        df = yf.download(
            tickers=symbol,
            period=f"{int(lookback_min/60)+1}h",
            interval=interval,
            progress=False,
            prepost=True,
        )
        if df.empty: return None
        return df.tail(lookback_min)
    except Exception as e:
        print("[yf]", e)
        return None

def _tp_sl_from_atr(entry: float, direction: str, atr: float, tp_pct: float, sl_pct: float):
    # ATRがNaNなら％で、あればATR換算（ラフに％↔ATRをブリッジ）
    if pd.isna(atr):
        if direction == "BUY":
            return entry * (1 + tp_pct), entry * (1 - sl_pct)
        else:
            return entry * (1 - tp_pct), entry * (1 + sl_pct)
    # ATR→％換算（0.01=1%想定スケール）
    tp = entry + atr * (tp_pct / 0.01) if direction == "BUY" else entry - atr * (tp_pct / 0.01)
    sl = entry - atr * (sl_pct / 0.01) if direction == "BUY" else entry + atr * (sl_pct / 0.01)
    return tp, sl

def _atr(symbol: str):
    df = _fetch(symbol, lookback_min=60)
    if df is None or len(df) < 5: return None
    atr = (df["High"] - df["Low"]).rolling(14).mean().iloc[-1]
    return atr

def _meets_entry(entry: float, last_close: float, direction: str, threshold: float):
    if direction == "BUY":
        return (last_close - entry) >= threshold
    return (entry - last_close) >= threshold

def _latest_ohlc(symbol: str):
    df = _fetch(symbol, lookback_min=3)
    if df is None or df.empty: return None
    last = df.iloc[-1]
    return float(last["High"]), float(last["Low"]), float(last["Close"])

async def _track_trade(symbol: str, direction: str, entry: float, tp: float, sl: float,
                       track_minutes: int, agent: str, notify: bool):
    start = time.time()
    deadline = start + track_minutes * 60
    notified_start = False

    while time.time() < deadline:
        time.sleep(max(3, CHECK_INTERVAL_SEC))
        ohlc = _latest_ohlc(symbol)
        if not ohlc: continue
        hi, lo, close = ohlc

        # 初回だけ開始通知（選定銘柄のみ）
        if notify and not notified_start:
            await _discord(f"📊 追跡開始: {symbol} {direction} / TP {tp:.2f} / SL {sl:.2f} / {agent}")
            notified_start = True

        if direction == "BUY":
            if hi >= tp:
                if notify: await _discord(f"🎯 利確: {symbol} @ {tp:.2f} / {agent}")
                _append_trade({"ts": datetime.utcnow().isoformat(), "symbol": symbol, "dir": direction,
                               "entry": entry, "exit": tp, "outcome": "TP", "agent": agent})
                return
            if lo <= sl:
                if notify: await _discord(f"🛑 損切: {symbol} @ {sl:.2f} / {agent}")
                _append_trade({"ts": datetime.utcnow().isoformat(), "symbol": symbol, "dir": direction,
                               "entry": entry, "exit": sl, "outcome": "SL", "agent": agent})
                return
        else:
            if lo <= tp:
                if notify: await _discord(f"🎯 利確: {symbol} @ {tp:.2f} / {agent}")
                _append_trade({"ts": datetime.utcnow().isoformat(), "symbol": symbol, "dir": direction,
                               "entry": entry, "exit": tp, "outcome": "TP", "agent": agent})
                return
            if hi >= sl:
                if notify: await _discord(f"🛑 損切: {symbol} @ {sl:.2f} / {agent}")
                _append_trade({"ts": datetime.utcnow().isoformat(), "symbol": symbol, "dir": direction,
                               "entry": entry, "exit": sl, "outcome": "SL", "agent": agent})
                return

    # タイムアウト
    if notify: await _discord(f"⌛ TIMEOUT: {symbol}（{track_minutes}分・未決済）/ {agent}")
    _append_trade({"ts": datetime.utcnow().isoformat(), "symbol": symbol, "dir": direction,
                   "entry": entry, "exit": None, "outcome": "TIMEOUT", "agent": agent})

async def handle_tv_signal(symbol: str, direction: str, price: float, ts: int = 0):
    # TESTは何もしない
    if direction == "TEST": return

    # 銘柄ごとのパラメータ
    tp_pct, sl_pct, follow_min, track_min = get_params(symbol)
    atr = _atr(symbol)
    entry = float(price)

    # 初動→“本エントリー化”チェック（follow_min 分だけ様子見）
    selected = is_selected_today(symbol)

    # ENTRY判定しきい値（ATR基準／ATRが無ければ軽めに％代替）
    threshold = (atr * ENTRY_ATR_MUL) if (atr is not None and not pd.isna(atr)) else entry * 0.001

    # 初動フォロー
    deadline_follow = time.time() + follow_min * 60
    became_valid = False
    while time.time() < deadline_follow:
        time.sleep(max(3, CHECK_INTERVAL_SEC))
        ohlc = _latest_ohlc(symbol)
        if not ohlc: continue
        _, _, last_close = ohlc
        if _meets_entry(entry, last_close, direction, threshold):
            became_valid = True
            # 選定銘柄だけ「条件合致」通知
            if selected:
                await _discord(f"⏩ 条件合致: {symbol} {direction}（初動→本エントリー化）")
            break

    # 条件未合致なら：選定銘柄は通知なしで終了／非選定はそのまま裏学習用へも進めない
    if not became_valid:
        # ログだけ（学習用に“無効初動”として控えるならここに追記してもOK）
        return

    # 本エントリー用のTP/SLライン
    tp, sl = _tp_sl_from_atr(entry, direction, atr, tp_pct, sl_pct)

    # --- 選定：通知あり／ 非選定：通知なし（ただし学習は実施） ---
    notify = bool(selected and USE_SELECTION_ONLY)

    # ベース（fixed）エージェント
    await _track_trade(symbol, direction, entry, tp, sl, track_min, agent="fixed", notify=notify)

    # 裏学習：もう一個の“rt”エージェント（非選定は通知なし、選定でも通知は出さない）
    if LEARN_FROM_ALL:
        # 例：rtは固定よりTP少し伸ばし・SL少し広く（簡単な別パラメータ例）
        tp_rt = entry + (tp - entry) * 1.1 if direction == "BUY" else entry - (entry - tp) * 1.1
        sl_rt = entry - (entry - sl) * 1.1 if direction == "BUY" else entry + (sl - entry) * 1.1
        await _track_trade(symbol, direction, entry, tp_rt, sl_rt, track_min, agent="rt", notify=False)

async def healthcheck():
    try:
        with _db() as _:
            pass
        return True, "ok"
    except Exception as e:
        return False, str(e)
