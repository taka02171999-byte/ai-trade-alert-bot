# summary_report.py — 全期間まとめ＆月次ハイライトをDiscordへ（JST基準）
import os, csv
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import requests

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
CSV_TRADES = Path("logs") / "trades.csv"

# ===== 日本時間（JST, UTC+9）で集計・表示 =====
JST = timezone(timedelta(hours=9))

def jst_now():
    return datetime.now(timezone.utc).astimezone(JST)

def parse_iso_jst(s: str):
    """ISO8601 を JST の aware datetime へ。TZ なしは JST とみなす。epoch秒も可。"""
    if not s:
        return None
    # epoch
    try:
        if s.strip().isdigit() or (s.strip().replace('.','',1).isdigit() and s.count('.')<=1):
            return datetime.fromtimestamp(float(s), tz=JST)
    except Exception:
        pass
    # ISO8601
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None

def load_trades():
    if not CSV_TRADES.exists():
        return []
    rows = []
    with CSV_TRADES.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t = parse_iso_jst(row.get("close_ts") or "")
            if not t: continue
            agent = (row.get("agent") or "fixed").lower()
            symbol = (row.get("symbol") or "-").upper()
            try:
                pnl = float(row.get("pnl_pct") or 0.0)
            except Exception:
                pnl = 0.0
            rows.append({"close_ts": t, "agent": agent, "symbol": symbol, "pnl_pct": pnl})
    return rows

def agg(rows):
    total=0.0; wins=0; losses=0; cnt=0
    by_sym=defaultdict(float)
    for r in rows:
        p=r["pnl_pct"]; total+=p; cnt+=1
        if p>=0: wins+=1
        else: losses+=1
        by_sym[r["symbol"]]+=p
    winrate = (wins/cnt*100.0) if cnt>0 else 0.0
    profit_sum = sum(max(0.0, r["pnl_pct"]) for r in rows)
    loss_sum = sum(max(0.0, -r["pnl_pct"]) for r in rows)
    pf = (profit_sum/loss_sum) if loss_sum>0 else (profit_sum if profit_sum>0 else 0.0)
    top = sorted(by_sym.items(), key=lambda x:-x[1])[:3]
    return total, winrate, pf, cnt, top

def monthly_highlight(rows):
    by_month=defaultdict(float)
    for r in rows:
        key = r["close_ts"].strftime("%Y-%m")  # JSTの「年月」で集計
        by_month[key]+=r["pnl_pct"]
    return sorted(by_month.items(), key=lambda x:-x[1])[:2]

def post_embed(title, desc, color=0x1abc9c):
    if not DISCORD_WEBHOOK:
        print("[warn] DISCORD_WEBHOOK not set"); return
    now_jst_str = jst_now().strftime("%Y-%m-%d %H:%M JST")
    payload = {"embeds":[{
        "title": f"{title}  ({now_jst_str})",
        "description": desc,
        "color": color,
        "footer":{"text":"AIりんご式 サマリーレポート（JST）"}
    }]}
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
    print("discord:", r.status_code)

def main():
    rows = load_trades()
    if not rows:
        post_embed("📦 サマリー", "取引履歴がまだありません。", 0x95a5a6); return

    first = min(r["close_ts"] for r in rows).strftime("%Y-%m-%d")
    last  = max(r["close_ts"] for r in rows).strftime("%Y-%m-%d")

    total, winrate, pf, cnt, top = agg(rows)
    tops = monthly_highlight(rows)

    lines = []
    lines.append(f"期間(JST): **{first} ～ {last}**")
    lines.append(f"総損益: **{total:+.2f}%** / 勝率 **{winrate:.1f}%** / PF **{pf:.2f}** / 件数 **{cnt}**")
    if top:
        lines.append("上位銘柄:")
        for s,v in top:
            lines.append(f"・{s}: {v:+.2f}%")
    if tops:
        lines.append("月次ハイライト:")
        for m,v in tops:
            lines.append(f"・{m}: {v:+.2f}%")

    post_embed("📚 全期間まとめレポート", "\n".join(lines), 0x00b894)

if __name__ == "__main__":
    main()
