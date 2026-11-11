"""Builds site/index.html, a single self-contained dashboard page."""
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .engine import STATE, load_state, summary

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"


def build(cfg):
    state = load_state(cfg)
    bt_file = ROOT / "state" / "backtest.json"
    by = defaultdict(lambda: {"bets": 0, "turnover": 0.0, "profit": 0.0, "clv": 0.0})
    for b in state["settled"]:
        if b["result"] == "void":
            continue
        r = by[f"{b['league']} ({b['country']})"]
        r["bets"] += 1
        r["turnover"] += b["stake"]
        r["profit"] += b["profit"]
        r["clv"] += b["odds"] * b.get("close_fair_prob", b["fair_prob"]) - 1
    leagues = sorted(({"league": k, **v, "roi": v["profit"] / v["turnover"] if v["turnover"] else 0,
                       "clv": v["clv"] / v["bets"]} for k, v in by.items()), key=lambda r: -r["bets"])
    payload = {
        "demo": os.environ.get("BOT_SYNTHETIC") == "1",
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "currency": cfg["currency"], "start": state["start_bankroll"], "started": state["started"],
        "summary": summary(state), "history": state["history"][-500:],
        "open": sorted(state["open"], key=lambda b: b["commence"]),
        "settled": state["settled"][-40:][::-1], "scan": state["last_scan"], "leagues": leagues,
        "credits": state.get("credits_remaining"), "lastRun": (state["runs"] or [None])[-1],
        "backtest": json.loads(bt_file.read_text()) if bt_file.exists() else None,
        "rules": {k: cfg["strategy"][k] for k in ("min_edge", "max_edge", "min_odds", "max_odds", "devig")}
                 | {"kelly": cfg["staking"]["kelly_fraction"], "max_stake": cfg["staking"]["max_stake_pct"]},
    }
    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(TEMPLATE.replace("__DATA__", json.dumps(payload).replace("</", "<\\/")))
    print(f"dashboard -> {SITE / 'index.html'}")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Betting Bot Dashboard</title>
<style>
.viz-root{color-scheme:light;--surface-0:#f4f4f2;--surface-1:#fcfcfb;--border:#e3e2dd;--grid:#ecebe7;
 --text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#7a7974;
 --series-1:#2a78d6;--series-2:#eb6834;--good:#0ca30c;--critical:#d03b3b}
@media (prefers-color-scheme:dark){.viz-root{color-scheme:dark;--surface-0:#111110;--surface-1:#1a1a19;
 --border:#2e2e2c;--grid:#262624;--text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#8f8e86;
 --series-1:#3987e5;--series-2:#d95926}}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.viz-root{background:var(--surface-0);color:var(--text-primary);min-height:100vh;padding:24px}
.wrap{max-width:1100px;margin:0 auto}
header{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:16px}
h1{font-size:22px;margin:0}h2{font-size:15px;margin:0 0 12px}
.muted{color:var(--text-secondary)}.small{font-size:12px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:14px}
.tile .label{font-size:12px;color:var(--text-secondary)}
.tile .value{font-size:24px;font-weight:600;margin-top:2px;font-variant-numeric:tabular-nums}
.tile .sub{font-size:12px;color:var(--text-muted);margin-top:2px}
.pos{color:var(--good)}.neg{color:var(--critical)}
.banner{background:var(--surface-1);border:1px dashed var(--series-2);border-radius:10px;padding:10px 14px;margin-bottom:16px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);font-size:13px;white-space:nowrap}
th{color:var(--text-secondary);font-weight:500}td.num,th.num{text-align:right}
.scroll{overflow-x:auto}
.chart{position:relative}
.chart svg{display:block;width:100%;height:auto;overflow:visible}
.tip{position:absolute;pointer-events:none;background:var(--surface-1);border:1px solid var(--border);border-radius:8px;
 padding:6px 9px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.12);display:none;white-space:nowrap}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--text-secondary);margin-bottom:8px}
.legend i{display:inline-block;width:12px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
.pill{font-size:11px;padding:2px 7px;border-radius:99px;border:1px solid var(--border);color:var(--text-secondary)}
.pill.bet{border-color:var(--series-1);color:var(--text-primary)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
p{margin:0 0 8px}
</style></head>
<body><div class="viz-root"><div class="wrap" id="app"></div></div>
<script>
const D = __DATA__;
const $ = s => document.querySelector(s);
const money = (v, d=2) => (v<0?'-':'') + '$' + Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
const pct = (v, d=1) => (v>0?'+':'') + (v*100).toFixed(d) + '%';
const cls = v => v>0 ? 'pos' : v<0 ? 'neg' : '';
const when = s => new Date(s).toLocaleString(undefined,{weekday:'short',day:'numeric',month:'short',hour:'numeric',minute:'2-digit'});
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function tile(label, value, sub, c='') {
  return `<div class="tile"><div class="label">${label}</div><div class="value ${c}">${value}</div><div class="sub">${sub||''}</div></div>`;
}
function table(cols, rows, empty) {
  if (!rows.length) return `<p class="muted small">${empty}</p>`;
  return `<div class="scroll"><table><thead><tr>${cols.map(c=>`<th class="${c.num?'num':''}">${c.h}</th>`).join('')}</tr></thead><tbody>` +
    rows.map(r => `<tr>${cols.map(c=>`<td class="${c.num?'num':''} ${c.cls?c.cls(r):''}">${c.f(r)}</td>`).join('')}</tr>`).join('') +
    `</tbody></table></div>`;
}

// Line chart: series = [{name, color, pts:[[dateString, value]]}], one y-axis, crosshair tooltip.
function lineChart(el, series, baseline, fmt) {
  const W = 1000, H = 280, m = {l:64, r:16, t:12, b:28};
  const all = series.flatMap(s => s.pts.map(p => [new Date(p[0]).getTime(), p[1]]));
  if (all.length < 2) { el.innerHTML = '<p class="muted small">The chart appears after a couple of runs.</p>'; return; }
  let x0 = Math.min(...all.map(p=>p[0])), x1 = Math.max(...all.map(p=>p[0]));
  let ys = all.map(p=>p[1]).concat(baseline!=null?[baseline]:[]);
  let y0 = Math.min(...ys), y1 = Math.max(...ys), pad = (y1-y0)*0.08 || 1; y0 -= pad; y1 += pad;
  const X = t => m.l + (t-x0)/(x1-x0||1)*(W-m.l-m.r), Y = v => H-m.b - (v-y0)/(y1-y0)*(H-m.t-m.b);
  let g = '';
  for (let i=0;i<=4;i++){ const v=y0+(y1-y0)*i/4;
    g += `<line x1="${m.l}" x2="${W-m.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--grid)"/>` +
         `<text x="${m.l-8}" y="${Y(v)+4}" text-anchor="end" font-size="11" fill="var(--text-muted)">${fmt(v)}</text>`; }
  for (let i=0;i<=4;i++){ const t=x0+(x1-x0)*i/4;
    g += `<text x="${X(t)}" y="${H-8}" text-anchor="${i==0?'start':i==4?'end':'middle'}" font-size="11" fill="var(--text-muted)">${new Date(t).toLocaleDateString(undefined,{day:'numeric',month:'short',year:'2-digit'})}</text>`; }
  if (baseline!=null) g += `<line x1="${m.l}" x2="${W-m.r}" y1="${Y(baseline)}" y2="${Y(baseline)}" stroke="var(--text-muted)" stroke-dasharray="4 4"/>`;
  series.forEach(s => {
    const d = s.pts.map((p,i)=>`${i?'L':'M'}${X(new Date(p[0]).getTime()).toFixed(1)},${Y(p[1]).toFixed(1)}`).join('');
    g += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  });
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.map(s=>s.name).join(', '))}">${g}
    <line class="xh" y1="${m.t}" y2="${H-m.b}" stroke="var(--text-muted)" visibility="hidden"/>
    ${series.map((s,i)=>`<circle class="dot${i}" r="4.5" fill="${s.color}" stroke="var(--surface-1)" stroke-width="2" visibility="hidden"/>`).join('')}
    <rect x="${m.l}" y="${m.t}" width="${W-m.l-m.r}" height="${H-m.t-m.b}" fill="transparent"/></svg><div class="tip"></div>`;
  const svg = el.querySelector('svg'), tip = el.querySelector('.tip'), xh = el.querySelector('.xh');
  svg.addEventListener('mousemove', e => {
    const r = svg.getBoundingClientRect(), px = (e.clientX - r.left) / r.width * W;
    const t = x0 + (px - m.l) / (W-m.l-m.r) * (x1-x0);
    let lines = [], xx = null;
    series.forEach((s,i) => {
      let best = s.pts.reduce((a,p)=>Math.abs(new Date(p[0])-t)<Math.abs(new Date(a[0])-t)?p:a, s.pts[0]);
      const bx = X(new Date(best[0]).getTime()); xx = xx ?? bx;
      const dot = el.querySelector('.dot'+i); dot.setAttribute('cx', bx); dot.setAttribute('cy', Y(best[1])); dot.setAttribute('visibility','visible');
      lines.push(`${series.length>1?esc(s.name.split(' (')[0])+': ':''}<b>${fmt(best[1])}</b>`);
      if (i==0) lines.unshift(`<span class="muted">${new Date(best[0]).toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'})}</span>`);
    });
    xh.setAttribute('x1', xx); xh.setAttribute('x2', xx); xh.setAttribute('visibility','visible');
    tip.innerHTML = lines.join('<br>'); tip.style.display = 'block';
    const left = xx / W * r.width; tip.style.left = Math.min(left + 12, r.width - tip.offsetWidth) + 'px'; tip.style.top = '8px';
  });
  svg.addEventListener('mouseleave', () => { tip.style.display='none'; xh.setAttribute('visibility','hidden'); el.querySelectorAll('circle').forEach(c=>c.setAttribute('visibility','hidden')); });
}

const S = D.summary, R = D.rules;
let h = `<header><div><h1>Betting Bot</h1><div class="muted small">Paper betting since ${D.started?new Date(D.started).toLocaleDateString():'—'} · updated ${D.generated}</div></div>
  <div class="muted small">Odds API credits left: <b>${D.credits ?? '—'}</b></div></header>`;
if (D.demo) h += `<div class="banner small"><b>Demo data.</b> These numbers come from the built-in simulator, not real markets. Add an Odds API key to go live.</div>`;
h += `<div class="tiles">
  ${tile('Bankroll', money(S.equity), `started ${money(D.start,0)}`, cls(S.equity-D.start))}
  ${tile('Profit', money(S.profit), `ROI ${pct(S.roi)} on ${money(S.turnover,0)} staked`, cls(S.profit))}
  ${tile('Expected profit', money(S.expected_profit), 'what the maths predicted. Gap = luck')}
  ${tile('Closing line value', pct(S.avg_clv), `${(S.beat_close*100).toFixed(0)}% of bets beat the closing price`, cls(S.avg_clv))}
  ${tile('Bets', S.bets, `${S.open} open · ${(S.win_rate*100).toFixed(0)}% won`)}
  ${tile('Average edge taken', pct(S.avg_edge), `bets need ≥ ${pct(R.min_edge,0)}`)}
</div>`;
h += `<div class="card"><h2>Bankroll</h2><div class="legend"><span><i style="background:var(--series-1)"></i>Bankroll (cash + money on open bets)</span><span><i style="background:var(--text-muted)"></i>Starting bankroll</span></div><div class="chart" id="bank"></div></div>`;
h += `<div class="card"><h2>Open bets</h2>${table([
  {h:'Starts', f:r=>when(r.commence)}, {h:'League', f:r=>esc(r.league)}, {h:'Game', f:r=>esc(r.game)},
  {h:'Pick', f:r=>esc(r.selection)}, {h:'Book', f:r=>esc(r.book)}, {h:'Odds', num:1, f:r=>r.odds.toFixed(2)},
  {h:'Fair', num:1, f:r=>r.fair_odds.toFixed(2)}, {h:'Edge', num:1, f:r=>pct(r.edge)},
  {h:'CLV now', num:1, f:r=>pct(r.odds*r.close_fair_prob-1), cls:r=>cls(r.odds*r.close_fair_prob-1)},
  {h:'Stake', num:1, f:r=>money(r.stake)}], D.open, 'No open bets.')}</div>`;
h += `<div class="grid2"><div class="card"><h2>By league</h2>${table([
  {h:'League', f:r=>esc(r.league)}, {h:'Bets', num:1, f:r=>r.bets}, {h:'Profit', num:1, f:r=>money(r.profit), cls:r=>cls(r.profit)},
  {h:'ROI', num:1, f:r=>pct(r.roi), cls:r=>cls(r.roi)}, {h:'CLV', num:1, f:r=>pct(r.clv), cls:r=>cls(r.clv)}], D.leagues, 'No settled bets yet.')}</div>
  <div class="card"><h2>How it decides</h2>
  <p class="small">1. Pull every bookmaker's odds for each game and strip out their margin (${esc(R.devig)} method).</p>
  <p class="small">2. Average them into a fair probability, counting sharp books (Pinnacle, Betfair) more heavily.</p>
  <p class="small">3. Back a team only when an Australian bookmaker pays at least ${pct(R.min_edge,0)} more than fair, at odds ${R.min_odds}–${R.max_odds}.</p>
  <p class="small">4. Stake ${R.kelly}× Kelly, capped at ${(R.max_stake*100).toFixed(0)}% of the bankroll.</p>
  <p class="small muted">The number that matters most early on is closing line value: beating the final price consistently is the best evidence of a real edge, long before profit is meaningful.</p></div></div>`;
h += `<div class="card"><h2>Latest market scan</h2><p class="small muted">Value found on the last run, including spots the risk rules skipped.</p>${table([
  {h:'League', f:r=>esc(r.league)}, {h:'Game', f:r=>esc(r.game)}, {h:'Pick', f:r=>esc(r.selection)}, {h:'Book', f:r=>esc(r.book)},
  {h:'Odds', num:1, f:r=>r.odds.toFixed(2)}, {h:'Fair', num:1, f:r=>r.fair_odds.toFixed(2)}, {h:'Edge', num:1, f:r=>pct(r.edge)},
  {h:'', f:r=>`<span class="pill ${r.status=='bet'?'bet':''}">${esc(r.status)}</span>`}], D.scan, 'Nothing met the rules on the last run.')}
  ${D.lastRun && D.lastRun.skipped_for_budget.length ? `<p class="small muted" style="margin-top:8px">Skipped to save credits: ${D.lastRun.skipped_for_budget.map(esc).join(', ')}</p>`:''}</div>`;
h += `<div class="card"><h2>Recent results</h2>${table([
  {h:'Settled', f:r=>when(r.settled_at)}, {h:'League', f:r=>esc(r.league)}, {h:'Game', f:r=>esc(r.game)}, {h:'Pick', f:r=>esc(r.selection)},
  {h:'Odds', num:1, f:r=>r.odds.toFixed(2)}, {h:'Result', f:r=>esc(r.result)}, {h:'Profit', num:1, f:r=>money(r.profit), cls:r=>cls(r.profit)},
  {h:'CLV', num:1, f:r=>pct(r.odds*r.close_fair_prob-1), cls:r=>cls(r.odds*r.close_fair_prob-1)}], D.settled, 'No results yet.')}</div>`;
if (D.backtest) {
  const B = D.backtest, cols = ['var(--series-1)','var(--series-2)'];
  h += `<div class="card"><h2>Historical test: European football ${B.from.slice(0,4)}–${B.to.slice(0,4)}</h2>
    <p class="small muted">${B.games.toLocaleString()} games from the top 5 leagues. Fair price from Pinnacle's opening odds; same edge, odds and staking rules as the live bot.</p>
    <div class="tiles">${B.strategies.map(s=>tile(esc(s.name.split(' (')[0]), pct(s.roi), `${s.bets} bets · CLV ${pct(s.avg_clv)} · ${money(s.start,0)} → ${money(s.final_bankroll,0)}`, cls(s.roi))).join('')}</div>
    <div class="legend">${B.strategies.map((s,i)=>`<span><i style="background:${cols[i]}"></i>${esc(s.name)}</span>`).join('')}</div>
    <div class="chart" id="bt"></div></div>`;
}
$('#app').innerHTML = h;
lineChart($('#bank'), [{name:'Bankroll', color:'var(--series-1)', pts:D.history.map(p=>[p.t,p.equity])}], D.start, v=>money(v,0));
if (D.backtest) lineChart($('#bt'), D.backtest.strategies.map((s,i)=>({name:s.name, color:['var(--series-1)','var(--series-2)'][i], pts:s.curve})), D.backtest.strategies[0]?.start, v=>money(v,0));
</script></body></html>
"""
