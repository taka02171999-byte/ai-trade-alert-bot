# ai/trailing_ai.py
import time
from typing import Literal, Optional
import yfinance as yf
import pandas as pd
from sqlalchemy import create_engine
from ai.net_guard import BUCKET, CACHE, backoff_sleep
import pytz, datetime as dt

Direction = Literal["BUY","SELL"]

def _is_market_open_now(tz_str="Asia/Tokyo") -> bool:
    tz = pytz.timezone(tz_str)
    now = dt.datetime.now(tz)
    hm = now.hour * 100 + now.minute
    return (900 <= hm < 1130) or (1230 <= hm < 1500)

class StateStore:
    def __init__(self, path="data/state.db"):
        self.engine = create_engine(f"sqlite:///{path}", echo=False)
        with self.engine.begin() as conn:
            conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS positions(
                symbol TEXT PRIMARY KEY,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                entry_ts INTEGER NOT NULL
            )""")

    def get_position(self, symbol: str):
        with self.engine.begin() as conn:
            return conn.exec_driver_sql(
                "SELECT symbol,direction,entry_price,entry_ts FROM positions WHERE symbol=?",
                (symbol,)
            ).fetchone()

    def set_position(self, symbol: str, direction: str, entry_price: float, entry_ts: int):
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "REPLACE INTO positions(symbol,direction,entry_price,entry_ts) VALUES(?,?,?,?)",
                (symbol, direction, entry_price, entry_ts)
            )

    def close_position(self, symbol: str):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM positions WHERE symbol=?", (symbol,))

class TrailingAI:
    def __init__(self, store: StateStore, tp_pct: float, sl_pct: float, poll_secs: int = 45, max_minutes: int = 20):
        self.store = store
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.poll_secs = poll_secs
        self.max_minutes = max_minutes

    def _yf_symbol(self, symbol: str) -> str:
        # 必要に応じて .T 付与などをここで調整
        return symbol

    def _last_price(self, symbol: str) -> Optional[float]:
        # 1) キャッシュ
        cached = CACHE.get(symbol)
        if cached is not None:
            return cached

        # 2) レート整流
        BUCKET.wait()

        s = self._yf_symbol(symbol)
        attempt = 0
        while attempt < 5:
            try:
                df = yf.download(s, interval="1m", period="60m", progress=False, prepost=False, auto_adjust=False, threads=False)
                if df is not None and not df.empty:
                    p = float(df["Close"].iloc[-1])
                    CACHE.set(symbol, p)
                    return p
            except Exception:
                pass
            attempt += 1
            backoff_sleep(attempt)
        return None

    def run_once(self, symbol: str, direction: Direction, ref_price: float, ts: int, notify):
        # 場中のみ
        if not _is_market_open_now():
            notify(f"🕒 {symbol} は場外のため監視スキップ")
            return

        # 既存ポジチェック
        if self.store.get_position(symbol):
            notify(f"ℹ️ {symbol} 既存ポジ稼働中のため後追いはスキップ")
            return

        # INトリガ（ref_price ±0.05%）
        in_trigger = ref_price * (1 + (0.0005 if direction=="BUY" else -0.0005))
        start = time.time()

        while time.time() - start < self.max_minutes * 60:
            p = self._last_price(symbol)
            if p is None:
                time.sleep(1.6); continue

            pos = self.store.get_position(symbol)
            if pos is None:
                if (direction=="BUY" and p >= in_trigger) or (direction=="SELL" and p <= in_trigger):
                    self.store.set_position(symbol, direction, p, int(time.time()))
                    notify(f"✅ IN: {symbol} {direction} @ {p:.2f}")
                else:
                    time.sleep(self.poll_secs)
                    continue
            else:
                _, d, entry, _ = self.store.get_position(symbol)
                tp = entry * (1 + self.tp_pct/100.0) if d=="BUY" else entry * (1 - self.tp_pct/100.0)
                sl = entry * (1 - self.sl_pct/100.0) if d=="BUY" else entry * (1 + self.sl_pct/100.0)

                hit_tp = (p >= tp) if d=="BUY" else (p <= tp)
                hit_sl = (p <= sl) if d=="BUY" else (p >= sl)

                if hit_tp:
                    self.store.close_position(symbol)
                    notify(f"🎯 TP: {symbol} {d} @ {p:.2f} (entry {entry:.2f}, +{self.tp_pct:.2f}%)")
                    return
                if hit_sl:
                    self.store.close_position(symbol)
                    notify(f"🛑 SL: {symbol} {d} @ {p:.2f} (entry {entry:.2f}, -{self.sl_pct:.2f}%)")
                    return

                time.sleep(self.poll_secs)

        # タイムアウト
        if self.store.get_position(symbol) is None:
            notify(f"⏱️ 後追い監視タイムアウト: {symbol}（IN未達）")
        else:
            notify(f"⏱️ 後追い監視タイムアウト: {symbol}（ポジ維持中）")
