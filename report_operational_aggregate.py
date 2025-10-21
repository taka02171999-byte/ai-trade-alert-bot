# report_operational_aggregate.py — 実運用レポ（日/週/月/累計）+ 翌日の「厳選銘柄×採用AI」更新（JST/Discord）
import os, csv, json, re, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import requests

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
CSV_TRADES = Path("logs") / "trades.csv"
ACTIVE_AGENT_FILE = Path("active_agent.txt")
SELECTED_JSON  = Path("logs") / "selected_symbols.json"
OVERRIDES_JSON = Path("overrides_selected.json")   # 任意
NAME_CACHE = Path("symbol_names.json")
JST = timezone(timedelta(hours=9))

def now_jst(): return datetime.now(timezone.utc).astimezone(JST)
def parse_iso(s):
    try: return datetime.fromisoformat(s)
    except: return None

# --- 銘柄名キャッシュ（必要時のみ） ---
def _load_cache():
    if NAME_CACHE.exists():
        try: return json.loads(NAME_CACHE.read_text(encoding="utf-8"))
        except: return {}
    return {}
def _save_cache(c):
    try: NAME_CACHE.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    except: pass
def _resolve_name(symbol:str, cache:dict):
    if not symbol or not symbol.isdigit(): return None
    if symbol in cache: return cache[symbol]
    try:
        url = f"https://finance.yahoo.co.jp/quote/{symbol}.T"
        with urllib.request.urlopen(url, timeout=6) as res:
            html = res.read().decode("utf-8", errors="ignore")
        m = re.search(r"<title>([^（(]+)[（(]", html)
        if m:
            cache[symbol] = m.group(1).strip()
            _save_cache(cache); return cache[symbol]
    except Exception:
        return None
    return None

# --- 取引ロード（全期間） ---
def load_trades_all():
    rows=[]
    if not CSV_TRADES.exists(): return rows
    with CSV_TRADES.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t = parse_iso(r.get("close_ts") or "")
            if not t: continue
            sym = (r.get("symbol") or "-").upper()
            ag  = (r.get("agent") or "fixed").lower()
            try: pnl = float(r.get("pnl_pct") or 0.0)
            except: pnl = 0.0
            rows.append({"close_ts": t.astimezone(JST), "symbol": sym, "agent": ag, "pnl_pct": pnl})
    return rows

# --- 集計 ---
def agg(rows):
    total=0.0; wins=0; cnt=0; profit=0.0; loss=0.0
    for r in rows:
        p=r["pnl_pct"]; total+=p; cnt+=1
        if p>=0: wins+=1; profit+=p
        else: loss+=-p
    wr=(wins/cnt*100.0) if cnt>0 else 0.0
    pf=(profit/loss) if loss>0 else (profit if profit>0 else 0.0)
    return round(total,2), round(wr,1), round(pf,2), cnt

def within_days(rows, days):
    end = now_jst()
    start = end - timedelta(days=days)
    return [r for r in rows if r["close_ts"] >= start]

def within_today(rows):
    d = now_jst().date()
    return [r for r in rows if r["close_ts"].date()==d]

def within_this_month(rows):
    n = now_jst()
    y, m = n.year, n.month
    return [r for r in rows if (r["close_ts"].year==y and r["close_ts"].month==m)]

# --- 翌日の「厳選銘柄×採用AI」を作る（今日の実トレから推定＋overrides最優先） ---
def read_winner():
    try:
        v = ACTIVE_AGENT_FILE.read_text(encoding="utf-8").strip().lower()
        return v if v in ("fixed","rt") else None
    except: return None

def read_overrides():
    if not OVERRIDES_JSON.exists(): return {}
    try:
        raw = json.loads(OVERRIDES_JSON.read_text(encoding="utf-8"))
        return {str(k).upper(): str(v).lower() for k,v in raw.items() if str(v).lower() in ("fixed","rt")}
    except: return {}

def infer_selected_from_today(today_rows, winner):
    # その日の実トレのある銘柄だけ対象。AIの出現回数で簡易判定（winnerに寄与があれば優先）
    by = defaultdict(lambda: {"fixed":0,"rt":0})
    for r in today_rows:
        if r["agent"] in ("fixed","rt"):
            by[r["symbol"]][r["agent"]] += 1
    selected={}
    for sym, cnts in by.items():
        if winner and cnts.get(winner,0)>0:
            selected[sym] = winner
        else:
            other = "rt" if winner=="fixed" else "fixed"
            if cnts.get(other,0)>0:
                selected[sym] = other
    return selected

def save_selected(selected, overrides):
    # overrides最優先で上書きして保存
    out = dict(selected)
    out.update(overrides)
    if not out:
        # 何もなければ空のまま(=翌日は全銘柄通知)にしておく
        return
    SELECTED_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

# --- Discord ---
def post_embed(title, desc, color=0x00bcd4):
    if not DISCORD_WEBHOOK:
        print("[warn] DISCORD_WEBHOOK not set"); return
    payload={"embeds":[{"title":title,"description":desc,"color":color,
                        "timestamp":datetime.now(timezone.utc).isoformat(),
                        "footer":{"text":"AIりんご式 実運用（JST）"}}]}
    try:
        r=requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
        print("discord:", r.status_code)
    except Exception as e:
        print("[error] discord:", e)

def main():
    rows_all = load_trades_all()
    cache = _load_cache()

    # セグメント
    rows_today = within_today(rows_all)
    rows_7d    = within_days(rows_all, 7)
    rows_30d   = within_days(rows_all, 30)
    rows_month = within_this_month(rows_all)

    # 集計（実トレ値）
    t_today = agg(rows_today)
    t_7d    = agg(rows_7d)
    t_30d   = agg(rows_30d)
    t_month = agg(rows_month)
    t_total = agg(rows_all)

    # 銘柄トップ（今日の上位3）
    by_sym = defaultdict(float)
    for r in rows_today: by_sym[r["symbol"]] += r["pnl_pct"]
    top_today = sorted(by_sym.items(), key=lambda x:-x[1])[:3]

    # 銘柄名解決
    lines=[]
    dstr = now_jst().strftime("%Y-%m-%d")
    lines.append(f"期間: **{dstr} 時点** (JST)")
    def fmt(tag, t):
        return f"{tag}: {t[0]:+.2f}% / 勝率 {t[1]:.1f}% / PF {t[2]:.2f} / 件数 {t[3]}"
    lines.append(fmt("【本日】", t_today))
    lines.append(fmt("【直近7日】", t_7d))
    lines.append(fmt("【直近30日】", t_30d))
    lines.append(fmt("【今月】", t_month))
    lines.append(fmt("【全期間】", t_total))
    if top_today:
        lines.append("")
        lines.append("本日の上位銘柄:")
        for s,v in top_today:
            name = _resolve_name(s, cache)
            label = f"{s}{f'（{name}）' if name else ''}"
            lines.append(f"・{label}: {v:+.2f}%")

    post_embed("📊 実運用レポート（日/週/月/累計・実トレ値）", "\n".join(lines))

    # 翌日の厳選銘柄×採用AI を作成・保存（今日の実トレから）
    winner = read_winner() or "fixed"
    overrides = read_overrides()
    selected = infer_selected_from_today(rows_today, winner)
    save_selected(selected, overrides)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
