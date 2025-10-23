# orchestrator.py
import os, time, json
from pathlib import Path
from datetime import datetime, timezone, timedelta, date

import numpy as np
import pandas as pd
import yfinance as yf

JST = timezone(timedelta(hours=9))

# ---- ENV ----
YF_RETRY       = int(os.getenv("YF_RETRY", "3"))
YF_RETRY_WAIT  = float(os.getenv("YF_RETRY_WAIT", "1.2"))
YF_BATCH_SIZE  = int(os.getenv("YF_BATCH_SIZE", "12"))
YF_CACHE_SEC   = int(os.getenv("YF_CACHE_SEC", "60"))

CONFIRM_EPS_PCT = float(os.getenv("CONFIRM_EPS_PCT", "0.3"))  # %

TPSL_WINDOW_MIN = int(os.getenv("TPSL_WINDOW_MIN", "30"))
MONITOR_SEC     = int(os.getenv("MONITOR_SEC", "30"))

TP_FALLBACK_PCT = float(os.getenv("TP_FALLBACK_PCT", "0.6"))
SL_FALLBACK_PCT = float(os.getenv("SL_FALLBACK_PCT", "0.35"))

NOTIFY_ONLY_TOP10 = (os.getenv("NOTIFY_ONLY_TOP10", "true").lower() == "true")

DATA_DIR = Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = DATA_DIR / "yf_cache.json"
TOP10_FILE = DATA_DIR / "top10.json"
UNIVERSE_FILE = DATA_DIR / "universe.txt"
NAMES_FILE = DATA_DIR / "names.json"

# ---- cache in file ----
def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

_mem = _load_json(CACHE_FILE, {})
_names = _load_json(NAMES_FILE, {})

def now_jst() -> datetime:
    return datetime.now(timezone.utc).astimezone(JST)

def today_str() -> str:
    return now_jst().strftime("%Y-%m-%d")

def to_yf_symbol(s: str) -> str:
    s = (s or "").strip().upper()
    if s.endswith(".T"): return s
    # 285A/4568など
    if s.replace("A","").isdigit() or (len(s) > 1 and s[:-1].isdigit() and s[-1] == "A"):
        return s + ".T"
    return s

def _cache_get(sym: str):
    o = _mem.get(sym)
    if not o: return None
    if time.time() - o["ts"] <= YF_CACHE_SEC:
        return o["close"]
    return None

def _cache_put(sym: str, close: float):
    _mem[sym] = {"ts": time.time(), "close": float(close)}
    try:
        CACHE_FILE.write_text(json.dumps(_mem), encoding="utf-8")
    except Exception:
        pass

def fetch_last_close_bulk(symbols: list[str]) -> dict[str, float | None]:
    """yfinanceをバッチ+リトライ+キャッシュで。失敗はNoneで返す。"""
    out, need = {}, []
    for raw in symbols:
        yfs = to_yf_symbol(raw)
        v = _cache_get(yfs)
        if v is not None:
            out[raw] = v
        else:
            need.append((raw, yfs))
    if not need:
        return out

    for i in range(0, len(need), YF_BATCH_SIZE):
        batch = need[i:i+YF_BATCH_SIZE]
        yf_syms = [b[1] for b in batch]
        wait = YF_RETRY_WAIT
        for tr in range(YF_RETRY):
            try:
                df = yf.download(
                    yf_syms, period="1d", interval="1m",
                    progress=False, auto_adjust=True, threads=False
                )
                if df is None or (hasattr(df, "empty") and df.empty):
                    raise RuntimeError("empty df")

                if isinstance(df.columns, pd.MultiIndex):
                    closes = df["Close"].iloc[-1].to_dict()
                else:
                    closes = {yf_syms[0]: float(df["Close"].iloc[-1])}

                for raw, yfs in batch:
                    v = closes.get(yfs)
                    if v is None or (isinstance(v, float) and np.isnan(v)):
                        out.setdefault(raw, None)
                    else:
                        out[raw] = float(v)
                        _cache_put(yfs, float(v))
                break
            except Exception:
                time.sleep(wait)
                wait *= 1.8
                if tr == YF_RETRY - 1:
                    for raw, _ in batch:
                        out.setdefault(raw, None)

    return out

def is_entry_confirmed(trigger_price: float, live_price: float, eps_pct: float | None = None) -> bool:
    if live_price is None or trigger_price <= 0: return False
    eps = (eps_pct if eps_pct is not None else CONFIRM_EPS_PCT) / 100.0
    return abs(live_price - trigger_price) / trigger_price <= eps

def atr_based_tp_sl(symbol: str, entry_price: float) -> tuple[float, float]:
    """ATR20ベース。失敗時は%フォールバック。"""
    try:
        yfs = to_yf_symbol(symbol)
        df = yf.download(yfs, period="5d", interval="1m", progress=False, auto_adjust=True, threads=False)
        if df is None or df.empty:
            df = yf.download(yfs, period="14d", interval="5m", progress=False, auto_adjust=True, threads=False)
        if df is None or df.empty:
            raise RuntimeError("no df")

        high = df["High"].astype(float)
        low  = df["Low"].astype(float)
        close = df["Close"].astype(float)
        prev = close.shift(1)
        tr = pd.concat([(high-low).abs(), (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1)
        atr = tr.rolling(20).mean().iloc[-1]
        if atr is None or np.isnan(atr) or atr <= 0:
            raise RuntimeError("atr nan")

        tp = entry_price + atr * 1.1
        sl = entry_price - atr * 0.7
        return (float(tp), float(sl))
    except Exception:
        tp = entry_price * (1.0 + TP_FALLBACK_PCT/100.0)
        sl = entry_price * (1.0 - SL_FALLBACK_PCT/100.0)
        return (float(tp), float(sl))

def load_universe() -> list[str]:
    if UNIVERSE_FILE.exists():
        syms = [s.strip() for s in UNIVERSE_FILE.read_text(encoding="utf-8").splitlines() if s.strip()]
        return list(dict.fromkeys(syms))  # uniq保持
    return []

def load_today_top10() -> list[str]:
    """当日の選定10銘柄を返す。無ければ universe の先頭10をフォールバック。"""
    try:
        d = _load_json(TOP10_FILE, {})
        if d.get("date") == today_str() and isinstance(d.get("symbols"), list):
            syms = [str(s).strip().upper() for s in d["symbols"] if str(s).strip()]
            if syms: return syms[:10]
    except Exception:
        pass
    uni = load_universe()
    return uni[:10] if len(uni) >= 10 else uni

def is_in_today_top10(symbol: str) -> bool:
    if not NOTIFY_ONLY_TOP10:
        return True
    top10 = load_today_top10()
    return symbol.upper() in set([s.upper() for s in top10])

def get_display_name(symbol: str) -> str:
    """銘柄名の自動付与（キャッシュ）。失敗したらシンボルを返すだけ。"""
    sym = symbol.upper()
    if sym in _names: return _names[sym]
    try:
        yfs = to_yf_symbol(sym)
        t = yf.Ticker(yfs)
        name = (t.fast_info.get("shortName")
                if hasattr(t, "fast_info") and isinstance(t.fast_info, dict)
                else None)
        if not name:
            info = t.info or {}
            name = info.get("shortName") or info.get("longName")
        if name:
            _names[sym] = str(name)
            NAMES_FILE.write_text(json.dumps(_names, ensure_ascii=False), encoding="utf-8")
            return _names[sym]
    except Exception:
        pass
    return sym
