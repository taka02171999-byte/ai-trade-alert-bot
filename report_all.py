# report_all.py — 直近7/30日の fixed vs rt 比較 + 全期間サマリー + 勝者更新（JSTでDiscordへ）
import os, csv
from pathlib import Path
from datetime import datetime, timezone, timedelta
import requests
from collections import defaultdict

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "")
CSV_TRADES = Path("logs") / "trades.csv"
ACTIVE_AGENT_FILE = Path("active_agent.txt")
JST = timezone(timedelta(hours=9))

def parse_iso(s):
    try:
        return datetime.fromisoformat(s)
    except:
        return None

def load_trades():
    if not CSV_TRADES.exists(): return []
    rows=[]
    with CSV_TRADES.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t = parse_iso(row.get("close_ts") or "")
            if not t: continue
            rows.append({
                "close_ts": t.astimezone(JST),
                "agent": (row.get("agent") or "fixed").lower(),
                "symbol": (row.get("symbol") or "-").upper(),
                "pnl_pct": float(row.get("pnl_pct") or 0.0),
            })
    return rows

def slice_since(days, rows):
    now = datetime.now(timezone.utc).astimezone(JST)
    since = now - timedelta(days=days)
    return [r for r in rows if r["close_ts"] >= since]

def summarize(rows):
    by_agent = {}
    for r in rows:
        a = r["agent"]
        by_agent.setdefault(a, {"sum":0.0,"wins":0,"losses":0,"cnt":0,"profit_sum":0.0,"loss_sum":0.0})
        s = by_agent[a]; p = r["pnl_pct"]; s["sum"] += p; s["cnt"] += 1
        if p >= 0: s["wins"] += 1; s["profit_sum"] += p
        else: s["losses"] += 1; s["loss_sum"] += abs(p)
    lines=[]
    for a in sorted(by_agent.keys()):
        s = by_agent[a]
        wr = (s["wins"]/s["cnt"]*100.0) if s["cnt"]>0 else 0.0
        pf = (s["profit_sum"]/s["loss_sum"]) if s["loss_sum"]>0 else (s["profit_sum"] if s["profit_sum"]>0 else 0.0)
        lines.append((a, s["sum"], wr, pf, s["cnt"]))
    return lines

def pick_winner(lines_7d, lines_30d):
    def best(lines):
        if not lines: return None
        lines_sorted = sorted(lines, key=lambda x: (x[1], x[2], x[3]))  # sum, winrate, PF
        return lines_sorted[-1][0]
    b7 = best(lines_7d); b30 = best(lines_30d)
    if "rt" in (b7, b30): return "rt"
    if "fixed" in (b7, b30): return "fixed"
    return b7 or b30 or "fixed"

def fmt_lines(label, lines):
    order = {"fixed":0, "rt":1}
    lines = sorted(lines, key=lambda x: order.get(x[0], 9))
    if not lines: return f"・{label}\n  データなし"
    out = [f"・{label}"]
    for a, ssum, wr, pf, cnt in lines:
        tag = "fixed" if a=="fixed" else "rt"
        out.append(f"  {tag:5}: {ssum:+.2f}% / 勝率 {wr:.1f}% / PF {pf:.2f} / 件数 {cnt}")
    return "\n".join(out)

def post_embed(title, desc, color):
    if not DISCORD_WEBHOOK:
        print("[warn] DISCORD_WEBHOOK not set"); return
    payload={"embeds":[{"title":title,"description":desc,"color":color,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "footer":{"text":"AIりんご式 レポート"}}]}
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
    print("discord:", r.status_code)

def agg_all(rows):
    total=0.0; wins=0; cnt=0; profit=0.0; loss=0.0; by_sym=defaultdict(float)
    for r in rows:
        p=r["pnl_pct"]; total+=p; cnt+=1
        if p>=0: wins+=1; profit+=p
        else: loss+=-p
        by_sym[r["symbol"]]+=p
    wr=(wins/cnt*100.0) if cnt>0 else 0.0
    pf=(profit/loss) if loss>0 else (profit if profit>0 else 0.0)
    top = sorted(by_sym.items(), key=lambda x:-x[1])[:3]
    return total, wr, pf, cnt, top

def main():
    rows = load_trades()
    rows7 = slice_since(7, rows)
    rows30 = slice_since(30, rows)

    sum7 = summarize(rows7)
    sum30 = summarize(rows30)
    winner = pick_winner(sum7, sum30)

    # 勝者を active_agent.txt に反映
    ACTIVE_AGENT_FILE.write_text(winner, encoding="utf-8")

    # 見出しレポ
    desc=[]
    desc.append(fmt_lines("直近7日", sum7))
    desc.append("")
    desc.append(fmt_lines("直近30日", sum30))
    desc.append("")
    wn = "固定" if winner=="fixed" else "RT"
    desc.append(f"🏁 総合判定：**{wn}優勢**（翌日の通知は勝者AIに限定）")

    post_embed("📆 パフォーマンス比較（fixed vs rt）", "\n".join(desc), 0x7289da)

    # 全期間サマリーも送る
    if rows:
        total, wr, pf, cnt, top = agg_all(rows)
        lines=[]
        first=min(r["close_ts"] for r in rows).strftime("%Y-%m-%d")
        last =max(r["close_ts"] for r in rows).strftime("%Y-%m-%d")
        lines.append(f"期間: **{first} ～ {last}** (JST)")
        lines.append(f"総損益: **{total:+.2f}%** / 勝率 **{wr:.1f}%** / PF **{pf:.2f}** / 件数 **{cnt}**")
        if top:
            lines.append("上位銘柄:")
            for s,v in top:
                lines.append(f"・{s}: {v:+.2f}%")
        post_embed("📚 全期間まとめレポート", "\n".join(lines), 0x00b894)
    else:
        post_embed("📚 全期間まとめレポート", "取引履歴がまだありません。", 0x95a5a6)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
