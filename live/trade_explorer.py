"""
trade_explorer.py — Build an interactive, self-contained HTML "Trade Explorer".

For each trade (from autoresearch_trade_history) it reconstructs the round-trip,
attaches the bracket's take-profit / stop-loss levels, pulls daily OHLCV (yfinance),
computes entry/exit-quality metrics, and renders a TradingView-style candlestick
chart (entry / SL / TP lines + markers) with a filterable trades table and
aggregate views.

Metrics per trade:
  R-multiple = (exit - entry) / (entry - stop)         # result in units of risk
  MAE (R)    = (min low over hold - entry) / risk       # worst adverse excursion
  MFE (R)    = (max high over hold - entry) / risk       # best favorable excursion
  tp_touched / sl_touched  = did price's high/low reach the level during the hold
  post_exit_drift_pct      = price move from exit over the following ~5 bars

Read-only. Does not touch strategy.py / prepare.py or place any orders.

Usage:
  python -m live.trade_explorer                     # -> live/trade_explorer.html
  python -m live.trade_explorer --out /path/x.html
  python -m live.trade_explorer --pre-days 22 --post-buffer 10
"""

import sys
import json
import argparse
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from live.config import LOG_FILE
from live import db

log = logging.getLogger("trader.explorer")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])

VENDOR_JS = Path(__file__).parent / "vendor" / "lightweight-charts.standalone.production.js"


# ─────────────────────────────────────────────────────────────
# 1. LOAD TRADES (round-trips + open positions) WITH TP/SL LEVELS
# ─────────────────────────────────────────────────────────────

def load_orders(conn):
    """Return (all_rows, levels) where levels maps entry order_id -> {'tp','sl'}
    read from its bracket child legs (any status, since TP/SL legs often expire)."""
    import psycopg2.extras
    from live.db import _q
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f'SELECT order_id, parent_order_id, leg_role, symbol, side, order_type, '
            f'       order_class, qty, filled_qty, limit_price, stop_price, '
            f'       filled_avg_price, status, submitted_at, filled_at '
            f'FROM {_q("autoresearch_trade_history")} ORDER BY COALESCE(filled_at, submitted_at)'
        )
        rows = [dict(r) for r in cur.fetchall()]

    levels = defaultdict(dict)
    for r in rows:
        if r["parent_order_id"]:
            if r["leg_role"] == "take_profit" and r["limit_price"] is not None:
                levels[r["parent_order_id"]]["tp"] = float(r["limit_price"])
            elif r["leg_role"] == "stop_loss" and r["stop_price"] is not None:
                levels[r["parent_order_id"]]["sl"] = float(r["stop_price"])
    return rows, levels


def reconstruct(rows, levels):
    """FIFO-match filled buys to filled sells per symbol -> closed trades; leftover
    buy lots become open trades. Each trade carries entry TP/SL (from its bracket)."""
    filled = [r for r in rows if r["status"] == "filled" and r["filled_qty"]]
    lots = defaultdict(deque)
    closed, seq = [], 0
    for r in filled:
        sym = r["symbol"]
        qty = float(r["filled_qty"] or 0)
        px = float(r["filled_avg_price"] or 0)
        if qty <= 0:
            continue
        if r["side"] == "buy":
            lv = levels.get(r["order_id"], {})
            lots[sym].append(dict(qty=qty, price=px, at=r["filled_at"],
                                  order_id=r["order_id"], tp=lv.get("tp"), sl=lv.get("sl")))
        else:
            reason = _exit_reason(r)
            remain = qty
            while remain > 1e-9 and lots[sym]:
                lot = lots[sym][0]
                take = min(lot["qty"], remain)
                seq += 1
                closed.append(dict(
                    id=seq, symbol=sym, qty=take,
                    entry_price=lot["price"], entry_at=lot["at"],
                    exit_price=px, exit_at=r["filled_at"], exit_reason=reason,
                    tp=lot["tp"], sl=lot["sl"], status="closed",
                ))
                lot["qty"] -= take
                remain -= take
                if lot["qty"] < 1e-9:
                    lots[sym].popleft()

    open_trades = []
    for sym, dq in lots.items():
        for lot in dq:
            if lot["qty"] <= 1e-9:
                continue
            seq += 1
            open_trades.append(dict(
                id=seq, symbol=sym, qty=lot["qty"],
                entry_price=lot["price"], entry_at=lot["at"],
                exit_price=None, exit_at=None, exit_reason="OPEN",
                tp=lot["tp"], sl=lot["sl"], status="open",
            ))
    return closed + open_trades


def _exit_reason(sell_row):
    role = sell_row.get("leg_role")
    if role == "take_profit":
        return "TAKE-PROFIT"
    if role == "stop_loss":
        return "STOP-LOSS"
    if sell_row.get("order_type") == "market":
        return "STRATEGY SELL"
    return "SELL"


# ─────────────────────────────────────────────────────────────
# 2. OHLCV (daily) via yfinance
# ─────────────────────────────────────────────────────────────

def fetch_ohlcv(symbols, start, end):
    """Return {symbol: [{time:'YYYY-MM-DD', open,high,low,close,volume}, ...]}"""
    import yfinance as yf
    import pandas as pd
    out = {}
    for sym in symbols:
        try:
            df = yf.download(sym, start=start, end=end, progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.columns = [c.strip().title() for c in df.columns]
            bars = []
            for idx, row in df.iterrows():
                bars.append(dict(time=idx.strftime("%Y-%m-%d"),
                                 open=round(float(row["Open"]), 2),
                                 high=round(float(row["High"]), 2),
                                 low=round(float(row["Low"]), 2),
                                 close=round(float(row["Close"]), 2),
                                 volume=int(row["Volume"]) if not pd.isna(row["Volume"]) else 0))
            out[sym] = bars
            log.info("  OHLCV %s: %d bars", sym, len(bars))
        except Exception as e:
            log.warning("  OHLCV fetch failed for %s: %s", sym, e)
            out[sym] = []
    return out


# ─────────────────────────────────────────────────────────────
# 3. METRICS (entry/exit quality vs the price that followed)
# ─────────────────────────────────────────────────────────────

def _d(dtobj):
    return dtobj.strftime("%Y-%m-%d") if dtobj else None


def enrich(trades, ohlcv):
    for t in trades:
        bars = ohlcv.get(t["symbol"], [])
        entry_d = _d(t["entry_at"])
        exit_d = _d(t["exit_at"]) if t["exit_at"] else (bars[-1]["time"] if bars else entry_d)
        t["entry_date"] = entry_d
        t["exit_date"] = _d(t["exit_at"])
        entry = t["entry_price"]
        sl = t["sl"]
        risk = (entry - sl) if (sl is not None and entry is not None and entry > sl) else None
        t["risk_per_share"] = risk

        # window of bars during the hold [entry_date, exit_or_today]
        hold = [b for b in bars if entry_d and b["time"] >= entry_d and (not exit_d or b["time"] <= exit_d)]
        pnl = None
        if t["exit_price"] is not None and entry is not None:
            pnl = (t["exit_price"] - entry) * t["qty"]
        t["pnl"] = pnl
        t["pct"] = ((t["exit_price"] / entry - 1) * 100) if (t["exit_price"] and entry) else None
        t["r_multiple"] = ((t["exit_price"] - entry) / risk) if (t["exit_price"] and risk) else None

        if hold and risk:
            lo = min(b["low"] for b in hold)
            hi = max(b["high"] for b in hold)
            t["mae_r"] = (lo - entry) / risk
            t["mfe_r"] = (hi - entry) / risk
        else:
            t["mae_r"] = t["mfe_r"] = None
        t["tp_touched"] = bool(hold and t["tp"] is not None and max(b["high"] for b in hold) >= t["tp"])
        t["sl_touched"] = bool(hold and sl is not None and min(b["low"] for b in hold) <= sl)

        # hold days
        if t["entry_at"] and t["exit_at"]:
            t["hold_days"] = (t["exit_at"] - t["entry_at"]).days
        else:
            t["hold_days"] = None

        # post-exit drift: close ~5 bars after exit vs exit price
        t["post_exit_drift_pct"] = None
        if exit_d and t["exit_price"]:
            after = [b for b in bars if b["time"] > exit_d][:5]
            if after:
                t["post_exit_drift_pct"] = (after[-1]["close"] / t["exit_price"] - 1) * 100

        # serialize datetimes
        t["entry_at"] = entry_d
        t["exit_at"] = _d(t["exit_at"]) if t.get("exit_date") else None
    return trades


# ─────────────────────────────────────────────────────────────
# 4. HTML
# ─────────────────────────────────────────────────────────────

def build_html(trades, ohlcv, pre_days, post_buffer):
    lib = VENDOR_JS.read_text() if VENDOR_JS.exists() else ""
    payload = json.dumps({"trades": trades, "ohlcv": ohlcv,
                          "pre_days": pre_days, "post_buffer": post_buffer},
                         default=str)
    return _HTML_TEMPLATE.replace("/*__LIB__*/", lib).replace("/*__DATA__*/", payload)


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>R7 Trade Explorer</title>
<style>
  :root{--bg:#0e1116;--panel:#161b22;--line:#232a34;--fg:#e6edf3;--mut:#8b949e;
        --green:#26a69a;--red:#ef5350;--blue:#4c9aff;--amber:#e3b341;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  header{padding:10px 16px;border-bottom:1px solid var(--line);display:flex;
         gap:16px;align-items:center;flex-wrap:wrap}
  header h1{font-size:15px;margin:0;font-weight:600}
  .filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  select,input{background:var(--panel);color:var(--fg);border:1px solid var(--line);
       border-radius:6px;padding:5px 8px;font-size:12px}
  .wrap{display:grid;grid-template-columns:minmax(360px,1fr) 2fr;gap:0;height:calc(100vh - 52px)}
  .left{border-right:1px solid var(--line);overflow:auto}
  .right{display:flex;flex-direction:column;overflow:hidden}
  table{width:100%;border-collapse:collapse}
  th,td{padding:6px 8px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
  th{position:sticky;top:0;background:var(--panel);cursor:pointer;font-weight:600;color:var(--mut);
     font-size:11px;text-transform:uppercase;letter-spacing:.03em}
  th:first-child,td:first-child{text-align:left}
  tbody tr{cursor:pointer}
  tbody tr:hover{background:#1c2330}
  tr.sel{background:#243044!important}
  .pos{color:var(--green)}.neg{color:var(--red)}.mut{color:var(--mut)}
  .tag{padding:1px 6px;border-radius:10px;font-size:10px;font-weight:600}
  .t-tp{background:rgba(38,166,154,.18);color:var(--green)}
  .t-sl{background:rgba(239,83,80,.18);color:var(--red)}
  .t-ss{background:rgba(76,154,255,.18);color:var(--blue)}
  .t-open{background:rgba(227,179,65,.18);color:var(--amber)}
  #chart{flex:1;min-height:280px}
  .meta{border-top:1px solid var(--line);padding:10px 14px;display:flex;gap:20px;flex-wrap:wrap;
        background:var(--panel)}
  .kv{display:flex;flex-direction:column;gap:1px}
  .kv .k{color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.03em}
  .kv .v{font-size:15px;font-weight:600}
  .agg{padding:8px 14px;border-top:1px solid var(--line);display:flex;gap:24px;flex-wrap:wrap;
       background:#0b0e13}
  .stat{display:flex;flex-direction:column}.stat .k{color:var(--mut);font-size:10px}
  .stat .v{font-size:14px;font-weight:600}
  .hint{color:var(--mut);font-size:11px;padding:6px 14px}
</style></head>
<body>
<header>
  <h1>📈 R7 Trade Explorer</h1>
  <div class="filters">
    <select id="f-symbol"><option value="">All tickers</option></select>
    <select id="f-reason">
      <option value="">All exits</option>
      <option>TAKE-PROFIT</option><option>STOP-LOSS</option>
      <option>STRATEGY SELL</option><option>OPEN</option>
    </select>
    <select id="f-outcome"><option value="">All outcomes</option>
      <option value="win">Winners</option><option value="loss">Losers</option></select>
    <input id="f-from" type="date" title="from entry date"/>
    <input id="f-to" type="date" title="to entry date"/>
    <span id="count" class="mut"></span>
  </div>
</header>
<div class="agg" id="agg"></div>
<div class="wrap">
  <div class="left">
    <table id="tbl"><thead><tr>
      <th data-k="symbol">Symbol</th><th data-k="entry_date">Entry</th>
      <th data-k="entry_price">Entry $</th><th data-k="exit_price">Exit $</th>
      <th data-k="pnl">P&L</th><th data-k="pct">%</th>
      <th data-k="r_multiple">R</th><th data-k="mae_r">MAE</th><th data-k="mfe_r">MFE</th>
      <th data-k="hold_days">Hold</th><th data-k="exit_reason">Exit</th>
    </tr></thead><tbody></tbody></table>
  </div>
  <div class="right">
    <div id="chart"></div>
    <div class="meta" id="meta"></div>
    <div class="hint">Entry ▲ · Exit ▼ · blue=entry · red=stop-loss · green=take-profit ·
      shaded=holding period. Scroll/drag the chart to pan; default view starts ~1 month before entry.</div>
  </div>
</div>
<script>/*__LIB__*/</script>
<script>
const DATA = /*__DATA__*/;
const T = DATA.trades, OHLCV = DATA.ohlcv, PRE = DATA.pre_days, POST = DATA.post_buffer;
let sortK="entry_date", sortDir=-1, cur=null, chart=null, series=null, plines=[];

const fmt=(v,d=2)=>v==null?"—":(+v).toFixed(d);
const money=v=>v==null?"—":(v<0?"-$":"$")+Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
const cls=v=>v==null?"mut":(v>=0?"pos":"neg");
const reasonTag=r=>{const m={"TAKE-PROFIT":"t-tp","STOP-LOSS":"t-sl","STRATEGY SELL":"t-ss","OPEN":"t-open"}[r]||"t-ss";
  return `<span class="tag ${m}">${r}</span>`;};

// symbol filter options
[...new Set(T.map(t=>t.symbol))].sort().forEach(s=>{
  const o=document.createElement("option");o.value=s;o.textContent=s;document.getElementById("f-symbol").appendChild(o);});

function filtered(){
  const sym=f_symbol.value, rs=f_reason.value, oc=f_outcome.value,
        from=f_from.value, to=f_to.value;
  return T.filter(t=>{
    if(sym&&t.symbol!==sym)return false;
    if(rs&&t.exit_reason!==rs)return false;
    if(oc==="win"&&!(t.pnl>0))return false;
    if(oc==="loss"&&!(t.pnl<=0&&t.pnl!=null))return false;
    if(from&&t.entry_date<from)return false;
    if(to&&t.entry_date>to)return false;
    return true;});
}
function sorted(rows){
  return rows.slice().sort((a,b)=>{
    let x=a[sortK],y=b[sortK];
    if(x==null)return 1; if(y==null)return -1;
    if(typeof x==="string")return sortDir*x.localeCompare(y);
    return sortDir*(x-y);});
}
function renderAgg(rows){
  const cl=rows.filter(t=>t.status==="closed");
  const w=cl.filter(t=>t.pnl>0), l=cl.filter(t=>t.pnl<=0);
  const tot=cl.reduce((s,t)=>s+(t.pnl||0),0);
  const rs=cl.filter(t=>t.r_multiple!=null).map(t=>t.r_multiple);
  const avgR=rs.length?rs.reduce((a,b)=>a+b,0)/rs.length:null;
  const tpT=cl.filter(t=>t.tp_touched).length, slT=cl.filter(t=>t.sl_touched).length;
  agg.innerHTML=[
    ["Closed",cl.length],["Realized P&L",money(tot)],
    ["Win rate",cl.length?Math.round(w.length/cl.length*100)+"%":"—"],
    ["Avg R",avgR==null?"—":fmt(avgR)+"R"],
    ["Avg win",w.length?money(w.reduce((s,t)=>s+t.pnl,0)/w.length):"—"],
    ["Avg loss",l.length?money(l.reduce((s,t)=>s+t.pnl,0)/l.length):"—"],
    ["TP touched",tpT+"/"+cl.length],["SL touched",slT+"/"+cl.length],
  ].map(([k,v])=>`<div class="stat"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("");
}
function renderTable(){
  const rows=sorted(filtered());
  document.getElementById("count").textContent=rows.length+" trades";
  renderAgg(rows);
  const tb=document.querySelector("#tbl tbody");tb.innerHTML="";
  rows.forEach(t=>{
    const tr=document.createElement("tr");if(cur&&t.id===cur.id)tr.className="sel";
    tr.innerHTML=`<td>${t.symbol}</td><td>${t.entry_date||"—"}</td>
      <td>${fmt(t.entry_price)}</td><td>${t.exit_price==null?"—":fmt(t.exit_price)}</td>
      <td class="${cls(t.pnl)}">${money(t.pnl)}</td>
      <td class="${cls(t.pct)}">${t.pct==null?"—":fmt(t.pct)+"%"}</td>
      <td class="${cls(t.r_multiple)}">${t.r_multiple==null?"—":fmt(t.r_multiple)+"R"}</td>
      <td class="${cls(t.mae_r)}">${fmt(t.mae_r)}</td>
      <td class="${cls(t.mfe_r)}">${fmt(t.mfe_r)}</td>
      <td>${t.hold_days==null?"—":t.hold_days+"d"}</td>
      <td style="text-align:left">${reasonTag(t.exit_reason)}</td>`;
    tr.onclick=()=>selectTrade(t);
    tb.appendChild(tr);});
  if(!cur&&rows.length)selectTrade(rows[0]);
}
function ensureChart(){
  if(chart)return;
  chart=LightweightCharts.createChart(document.getElementById("chart"),{
    layout:{background:{color:"#0e1116"},textColor:"#e6edf3"},
    grid:{vertLines:{color:"#1b212b"},horzLines:{color:"#1b212b"}},
    rightPriceScale:{borderColor:"#232a34"},timeScale:{borderColor:"#232a34"},
    crosshair:{mode:0}});
  series=chart.addCandlestickSeries({upColor:"#26a69a",downColor:"#ef5350",
    borderUpColor:"#26a69a",borderDownColor:"#ef5350",
    wickUpColor:"#26a69a",wickDownColor:"#ef5350"});
  new ResizeObserver(()=>chart.applyOptions({})).observe(document.getElementById("chart"));
}
function selectTrade(t){
  cur=t; ensureChart();
  plines.forEach(p=>series.removePriceLine(p));plines=[];
  const bars=(OHLCV[t.symbol]||[]);
  series.setData(bars);
  // markers
  const marks=[];
  if(t.entry_date)marks.push({time:t.entry_date,position:"belowBar",color:"#4c9aff",shape:"arrowUp",text:"Entry "+fmt(t.entry_price)});
  if(t.exit_date)marks.push({time:t.exit_date,position:"aboveBar",
     color:t.exit_reason==="STOP-LOSS"?"#ef5350":(t.exit_reason==="TAKE-PROFIT"?"#26a69a":"#4c9aff"),
     shape:"arrowDown",text:(t.exit_reason||"Exit")+" "+fmt(t.exit_price)});
  series.setMarkers(marks);
  // price lines
  const L=(price,color,title)=>{if(price!=null)plines.push(series.createPriceLine(
     {price:+price,color,lineWidth:1,lineStyle:2,axisLabelVisible:true,title}));};
  L(t.entry_price,"#4c9aff","Entry");
  L(t.sl,"#ef5350","SL"); L(t.tp,"#26a69a","TP");
  // default visible window: ~PRE trading days before entry -> exit + POST buffer (or last bar)
  if(bars.length){
    const idx=Math.max(0,bars.findIndex(b=>b.time>=t.entry_date));
    const from=bars[Math.max(0,idx-PRE)].time;
    let toI=t.exit_date?bars.findIndex(b=>b.time>=t.exit_date):bars.length-1;
    if(toI<0)toI=bars.length-1;
    const to=bars[Math.min(bars.length-1,toI+POST)].time;
    chart.timeScale().setVisibleRange({from,to});
  }
  renderMeta(t);
  document.querySelectorAll("#tbl tbody tr").forEach(r=>r.classList.remove("sel"));
  renderTable();
}
function renderMeta(t){
  const rows=[
    ["Ticker",t.symbol],["Entry",`${fmt(t.entry_price)} · ${t.entry_date||"—"}`],
    ["Exit",t.exit_price==null?"— (open)":`${fmt(t.exit_price)} · ${t.exit_date||"—"}`],
    ["Stop / Target",`${fmt(t.sl)} / ${fmt(t.tp)}`],
    ["P&L",money(t.pnl)],["R-multiple",t.r_multiple==null?"—":fmt(t.r_multiple)+"R"],
    ["MAE / MFE",`${fmt(t.mae_r)}R / ${fmt(t.mfe_r)}R`],
    ["TP / SL touched",`${t.tp_touched?"yes":"no"} / ${t.sl_touched?"yes":"no"}`],
    ["Post-exit drift",t.post_exit_drift_pct==null?"—":fmt(t.post_exit_drift_pct)+"%"],
    ["Exit reason",t.exit_reason],
  ];
  meta.innerHTML=rows.map(([k,v])=>`<div class="kv"><span class="k">${k}</span><span class="v">${v}</span></div>`).join("");
}
// wire filters + sort
["f-symbol","f-reason","f-outcome","f-from","f-to"].forEach(id=>
  document.getElementById(id).addEventListener("input",()=>{cur=null;renderTable();}));
document.querySelectorAll("#tbl th").forEach(th=>th.addEventListener("click",()=>{
  const k=th.dataset.k;if(sortK===k)sortDir*=-1;else{sortK=k;sortDir=(k==="symbol"||k==="entry_date")?1:-1;}
  renderTable();}));
window.f_symbol=document.getElementById("f-symbol");window.f_reason=document.getElementById("f-reason");
window.f_outcome=document.getElementById("f-outcome");window.f_from=document.getElementById("f-from");
window.f_to=document.getElementById("f-to");window.agg=document.getElementById("agg");
window.meta=document.getElementById("meta");
renderTable();
</script>
</body></html>"""


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def generate(out_path, pre_days=22, post_buffer=10, history_days=120, conn=None):
    """Build the Trade Explorer HTML and write it to `out_path`. Reusable entry
    point (called by the daily automation job and by the CLI). Returns the path,
    or None if there were no trades. Opens its own DB connection if none given."""
    own = conn is None
    if own:
        conn = db.connect()
    try:
        db.ensure_trade_history_table(conn)
        rows, levels = load_orders(conn)
        trades = reconstruct(rows, levels)
    finally:
        if own:
            conn.close()
    if not trades:
        log.warning("No trades found in autoresearch_trade_history.")
        return None

    symbols = sorted({t["symbol"] for t in trades})
    entry_dates = [t["entry_at"] for t in trades if t["entry_at"]]
    start = (min(entry_dates) - timedelta(days=history_days)).strftime("%Y-%m-%d")
    end = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    log.info("Fetching OHLCV for %d symbols (%s .. %s)", len(symbols), start, end)
    ohlcv = fetch_ohlcv(symbols, start, end)

    trades = enrich(trades, ohlcv)
    html = build_html(trades, ohlcv, pre_days, post_buffer)
    Path(out_path).write_text(html)
    log.info("Wrote %s (%d trades, %.0f KB)", out_path, len(trades), len(html) / 1024)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Build interactive Trade Explorer HTML")
    ap.add_argument("--out", default=str(Path(__file__).parent / "trade_explorer.html"))
    ap.add_argument("--pre-days", type=int, default=22, help="visible bars before entry (~1 month)")
    ap.add_argument("--post-buffer", type=int, default=10, help="visible bars after exit")
    ap.add_argument("--history-days", type=int, default=120,
                    help="extra daily history to load before the earliest entry (for panning)")
    args = ap.parse_args()
    generate(args.out, pre_days=args.pre_days, post_buffer=args.post_buffer,
             history_days=args.history_days)


if __name__ == "__main__":
    main()
