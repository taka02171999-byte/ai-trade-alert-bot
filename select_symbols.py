# select_symbols.py — 翌日の「推奨銘柄リスト」を自動作成 + 名称先読みキャッシュ（JST）
import os, csv, json, math, statistics, re, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta
import requests

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
TOP_K = int(os.getenv("TOP_K", "20"))
MIN_TRADES_30D = int(os.getenv("MIN_TRADES_30D", "3"))
CSV_TRADES  = Path("logs") / "trades.csv"
CSV_SIGNALS = Path("logs") / "signals.csv"
OUT_TXT = Path("watchlist.txt")
OUT_JSON = Path("watchlist.json")
SYMBOL_CACHE_FILE = Path("symbol_names.json")
JST = timezone(timedelta(hours=9))

def jst_now(): return datetime.now(timezone.utc).astimezone(JST)

def parse_ts(s):
    if not s: return None
    try:
        if s.strip().replace('.','',1).isdigit():
            return datetime.fromtimestamp(float(s), tz=JST)
    except: pass
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None: dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except: return None

def to_f(x, default=None):
    try: return float(x)
    except: return default

def post_discord(title, desc, color=0x00b894):
    if not DISCORD_WEBHOOK: return
    payload = {"embeds":[{"title":title,"description":desc,"color":color,
                          "footer":{"text":"AIりんご式 Watchlist | " + jst_now().strftime("%Y-%m-%d %H:%M JST")}}]}
    requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)

def load_trades(days=30):
    rows=[]; since = jst_now() - timedelta(days=days)
    if not CSV_TRADES.exists(): return rows
    with CSV_TRADES.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = parse_ts(r.get("close_ts") or "")
            if not t or t < since: continue
            rows.append({
                "close_ts": t,
                "symbol": (r.get("symbol") or "-").upper(),
                "pnl_pct": to_f(r.get("pnl_pct"), 0.0)
            })
    return rows

def load_signals(days=20):
    rows=[]; since = jst_now() - timedelta(days=days)
    if not CSV_SIGNALS.exists(): return rows
    with CSV_SIGNALS.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = parse_ts(r.get("ts") or "")
            if not t or t < since: continue
            rows.append({
                "ts": t,
                "symbol": (r.get("symbol") or "-").upper(),
                "c": to_f(r.get("c"), None),
                "v": to_f(r.get("v"), None),
                "atr": to_f(r.get("atr"), None)
            })
    return rows

def zscore(x, a, b):
    if a is None or b is None or a==b: return 0.0
    t = (x - a) / (b - a)
    return max(0.0, min(1.0, t))

def score_symbols(trades30, signals20):
    by_sym_tr, by_sym_sig = {}, {}
    for r in trades30:
        s = r["symbol"]
        o = by_sym_tr.setdefault(s, {"pnl":0.0,"cnt":0,"wins":0,"seq_losses":0,"last_loss_streak":0})
        p = r["pnl_pct"] or 0.0
        o["pnl"] += p; o["cnt"] += 1
        if p >= 0: o["wins"] += 1; o["seq_losses"] = 0
        else: o["seq_losses"] += 1; o["last_loss_streak"] = o["seq_losses"]
    for r in signals20:
        s = r["symbol"]
        o = by_sym_sig.setdefault(s, {"vols":[], "volumes":[]})
        if r["c"] and r["atr"] and r["c"]>0 and r["atr"]>0:
            o["vols"].append(r["atr"]/r["c"])
        if r["v"]: o["volumes"].append(r["v"])

    pnl_vals = [v["pnl"] for v in by_sym_tr.values()] or [0]
    wr_vals  = [v["wins"]/v["cnt"] for v in by_sym_tr.values() if v["cnt"]>0] or [0]
    vol_vals = [statistics.median(v["vols"]) for v in by_sym_sig.values() if v["vols"]] or [0.01]
    volu_vals= [statistics.median(v["volumes"]) for v in by_sym_sig.values() if v["volumes"]] or [1]
    pnl_min, pnl_max = min(pnl_vals), max(pnl_vals)
    wr_min,  wr_max  = min(wr_vals),  max(wr_vals)
    vol_min, vol_max = min(vol_vals), max(vol_vals)
    volu_min,volu_max= min(volu_vals),max(volu_vals)

    results=[]; symbols = sorted(set(list(by_sym_tr.keys()) + list(by_sym_sig.keys())))
    for s in symbols:
        tr = by_sym_tr.get(s, {"pnl":0.0,"cnt":0,"wins":0,"last_loss_streak":0})
        sig= by_sym_sig.get(s, {"vols":[],"volumes":[]})
        pnl = tr["pnl"]; wr = (tr["wins"]/tr["cnt"]) if tr["cnt"]>0 else 0.0
        vol = statistics.median(sig["vols"]) if sig["vols"] else None
        volu= statistics.median(sig["volumes"]) if sig["volumes"] else None

        sc_pnl = zscore(pnl, pnl_min, pnl_max)
        sc_wr  = zscore(wr,  wr_min,  wr_max)
        sc_vol = zscore((vol or 0.01), vol_min, vol_max)
        sc_volu= zscore(math.log(max(volu or 1,1)), math.log(max(volu_min,1)), math.log(max(volu_max,1)))

        penalty = 0.0
        if tr.get("last_loss_streak",0) >= 2: penalty += 0.1
        if tr.get("cnt",0) < MIN_TRADES_30D: penalty += 0.1

        score = 0.45*sc_pnl + 0.25*sc_wr + 0.15*sc_vol + 0.15*sc_volu - penalty
        results.append({
            "symbol": s, "score": round(score,4), "pnl30": round(pnl,2),
            "wr30": round(wr*100,1), "cnt30": tr.get("cnt",0),
            "vol_med": round((vol or 0.0)*100,2), "volu_med": int(volu or 0)
        })
    results.sort(key=lambda x: x["score"], reverse=True)
    return results

def _load_symbol_cache():
    if SYMBOL_CACHE_FILE.exists():
        try: return json.loads(SYMBOL_CACHE_FILE.read_text(encoding="utf-8"))
        except: return {}
    return {}

def _save_symbol_cache(cache: dict):
    SYMBOL_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

def prefetch_symbol_names(symbols):
    cache = _load_symbol_cache(); updated = 0
    for s in symbols:
        if not s or not s.isdigit() or s in cache: continue
        try:
            url = f"https://finance.yahoo.co.jp/quote/{s}.T"
            with urllib.request.urlopen(url, timeout=5) as res:
                html = res.read().decode("utf-8", errors="ignore")
            m = re.search(r"<title>([^（(]+)[（(]", html)
            if m: cache[s] = m.group(1).strip(); updated += 1
        except Exception as e:
            print(f"[warn] prefetch {s}: {e}")
    if updated: _save_symbol_cache(cache)
    return updated

def main():
    tr = load_trades(days=30)
    sg = load_signals(days=20)
    ranked = score_symbols(tr, sg)
    top = ranked[:TOP_K]
    syms = [r["symbol"] for r in top]

    updated = prefetch_symbol_names(syms)
    if updated: print(f"[prefetch] symbol names updated: {updated}")

    OUT_TXT.write_text(",".join(syms) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps({"generated_at": jst_now().isoformat(),
                                    "top_k": TOP_K, "symbols": top}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"・{r['symbol']}: score {r['score']:.3f} / pnl30 {r['pnl30']:+.2f}% / wr {r['wr30']:.1f}% / cnt {r['cnt30']}" for r in top[:10]] or ["候補なし（データ不足）"]
    post_discord("🧩 翌日ウォッチリスト（自動選定）", "\n".join(lines))
    print("[watchlist]", syms)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
