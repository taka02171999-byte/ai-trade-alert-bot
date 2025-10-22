# optimize_percent.py — v3 (ATR + 出来高 + ブレイク幅 + クールダウン学習) ※時間帯なし
import os, csv, json, statistics, requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK","")
SIGNALS_URL     = os.getenv("SIGNALS_URL","")   # 例: https://<app>.onrender.com/signals
PARAMS_FILE     = Path("params.json")
SIG_LOCAL       = Path("logs")/"signals.csv"
JST = timezone(timedelta(hours=9))

def jst_iso():
    return datetime.now(timezone.utc).astimezone(JST).isoformat()

def notify(title, desc):
    if not DISCORD_WEBHOOK: return
    payload = {"embeds":[{"title":title,"description":desc,"color":0x1ABC9C,"timestamp":jst_iso(),"footer":{"text":"AIりんご式"}}]}
    try: requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
    except: pass

def to_f(x, d=0.0):
    try: return float(x)
    except: return d

def parse_ts(s):
    if not s: return None
    try:
        # ISO文字列（Z/オフセット対応）
        s2 = s.replace("Z","+00:00") if s.endswith("Z") else s
        return datetime.fromisoformat(s2)
    except:
        return None

def fetch_signals():
    rows=[]
    if SIGNALS_URL:
        try:
            r=requests.get(SIGNALS_URL,timeout=20)
            if r.ok and r.text.strip():
                rows=list(csv.DictReader(r.text.splitlines()))
        except: pass
    if not rows and SIG_LOCAL.exists():
        with SIG_LOCAL.open(newline="",encoding="utf-8") as f:
            rows=list(csv.DictReader(f))
    return rows

def learn(rows, old_params):
    # rows: ts, symbol, o,h,l,c,v,atr,side,tf...
    by_sym={}
    for r in rows:
        s=(r.get("symbol") or "UNKNOWN").upper()
        o,h,l,c = map(to_f,[r.get("o"),r.get("h"),r.get("l"),r.get("c")])
        v       = to_f(r.get("v"))
        atr     = to_f(r.get("atr"))
        side    = (r.get("side") or "").lower()
        ts      = parse_ts(r.get("ts"))
        if c<=0 or atr<=0 or v<=0: 
            continue
        d = by_sym.setdefault(s,{"atrp":[], "vol":[], "move":[], "ents":[]})
        d["atrp"].append(atr/c)                               # ボラ％
        d["vol"].append(v)                                    # 出来高
        d["move"].append(abs(c-o)/o if o>0 else 0.0)          # ブレイク度合い
        if side in ("buy","sell") and ts:
            d["ents"].append(ts)

    out={}
    for s,d in by_sym.items():
        if not d["atrp"]: 
            continue

        # --- ATR倍率（段階）
        v_med = statistics.median(d["atrp"])
        if   v_med <= 0.004: sl,tp = 0.7,1.2
        elif v_med <= 0.008: sl,tp = 0.8,1.4
        elif v_med <= 0.015: sl,tp = 0.9,1.6
        else:                 sl,tp = 1.1,2.0

        # --- 相対出来高しきい値（上位30%目安）
        vols = d["vol"]; med = statistics.median(vols)
        rels = sorted([x/med for x in vols if med>0])
        p70  = rels[int(0.7*len(rels))] if rels else 1.2
        min_vol_mult = round(max(1.1, min(3.0, p70)), 2)

        # --- ブレイク幅しきい値（バー伸び率の上位30%中央値）
        moves = sorted(d["move"])
        brk_p = moves[int(0.7*len(moves)):] or [0.004]
        min_break_pct = round(max(0.002, min(0.03, statistics.median(brk_p))), 4)

        # --- クールダウン学習（エントリー間隔のメジアンの半分を目安）
        cd_learn = 60
        ents = sorted([t for t in d["ents"] if isinstance(t, datetime)])
        if len(ents) >= 2:
            gaps = []
            for i in range(1, len(ents)):
                dt = (ents[i] - ents[i-1]).total_seconds()
                if dt > 0:
                    gaps.append(dt)
            if gaps:
                med_gap = statistics.median(gaps)
                cd_learn = int(max(30, min(180, med_gap * 0.5)))  # 30〜180秒にクリップ

        # 過去値があれば 70%:30% で平滑化
        old_cd = int((old_params.get(s) or {}).get("cooldown_sec", 60))
        cooldown_sec = int(round(0.7*old_cd + 0.3*cd_learn))

        out[s]={
            "sl_atr": round(sl,2),
            "tp_atr": round(tp,2),
            "min_vol_mult": min_vol_mult,
            "min_break_pct": min_break_pct,
            "cooldown_sec": cooldown_sec
        }
    return out

def main():
    rows = fetch_signals()
    old  = {}
    if PARAMS_FILE.exists():
        try: old=json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
        except: pass
    new = learn(rows, old_params=old)
    merged = dict(old); merged.update(new)
    PARAMS_FILE.write_text(json.dumps(merged,ensure_ascii=False,indent=2),encoding="utf-8")

    if new:
        lines=[f"・{s}: SL×{v['sl_atr']} / TP×{v['tp_atr']} / VOL≥{v['min_vol_mult']}x / BRK≥{v['min_break_pct']*100:.2f}% / CD={v['cooldown_sec']}s"
               for s,v in list(new.items())[:30]]
        notify("🤖 夜間学習レポート", "更新銘柄数: "+str(len(new))+"\n"+"\n".join(lines))
    else:
        notify("🤖 夜間学習レポート", "更新なし（データ不足or同一）")

if __name__=="__main__":
    main()
