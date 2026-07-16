"""Render scored candidates into one self-contained HTML file.

No server, no CDN, no build step: the data is embedded as JSON and the table
(sort / search / filter / per-property status + notes) is plain vanilla JS.
Status and notes persist in the browser's localStorage, so you can open the
file, work your target list, close it, and your annotations are still there.
"""
import json


def build_html(records, path):
    payload = json.dumps(records, default=str).replace("</", "<\\/")
    html = _TEMPLATE.replace("__DATA__", payload)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NYC Rent-Stabilized — Likely Sellers</title>
<style>
  :root{
    --bg:#0f1216; --panel:#171b21; --panel2:#1e242c; --line:#2a323c;
    --text:#e6edf3; --muted:#8b98a5; --accent:#4c9ffe; --good:#3fb950;
    --warn:#d29922; --bad:#f85149; --chip:#233;
  }
  @media (prefers-color-scheme: light){
    :root{--bg:#f6f8fa;--panel:#fff;--panel2:#f0f3f6;--line:#d0d7de;
      --text:#1f2328;--muted:#636c76;--accent:#0969da;--chip:#eaeef2;}
  }
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    background:var(--bg);color:var(--text)}
  header{padding:16px 20px;border-bottom:1px solid var(--line);background:var(--panel);
    position:sticky;top:0;z-index:20}
  h1{margin:0 0 4px;font-size:18px}
  .sub{color:var(--muted);font-size:12.5px}
  .stats{display:flex;gap:18px;flex-wrap:wrap;margin-top:10px;font-size:12.5px}
  .stat b{font-size:16px;display:block}
  .controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;padding:12px 20px;
    background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:19}
  .controls input[type=text],.controls select{background:var(--panel2);color:var(--text);
    border:1px solid var(--line);border-radius:6px;padding:6px 9px;font-size:13px}
  .controls label{display:flex;align-items:center;gap:5px;color:var(--muted);font-size:12.5px}
  .btn{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:7px 12px;
    font-size:13px;cursor:pointer}
  .btn.ghost{background:var(--panel2);color:var(--text);border:1px solid var(--line)}
  .wrap{overflow-x:auto}
  table{border-collapse:collapse;width:100%;font-size:12.5px}
  th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;vertical-align:top}
  th{position:sticky;top:0;background:var(--panel2);cursor:pointer;user-select:none;z-index:5}
  th.sorted::after{content:" \25B4";color:var(--accent)}
  th.sorted.desc::after{content:" \25BE"}
  tr:hover td{background:rgba(127,127,127,.06)}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  a{color:var(--accent);text-decoration:none}
  a:hover{text-decoration:underline}
  .score{font-weight:700;padding:2px 7px;border-radius:10px;color:#000}
  .flags{color:var(--muted);font-size:11.5px;white-space:normal;max-width:260px}
  .notes{width:150px;background:transparent;border:1px solid transparent;color:var(--text);
    border-radius:4px;padding:3px 5px;font:inherit;font-size:12px}
  .notes:focus{border-color:var(--line);background:var(--panel2);outline:none}
  select.status{background:var(--panel2);color:var(--text);border:1px solid var(--line);
    border-radius:5px;padding:3px 5px;font-size:12px}
  .pill{padding:1px 7px;border-radius:10px;font-size:11px;white-space:nowrap}
  .addr{font-weight:600}
  .muted{color:var(--muted)}
  .hidden{display:none}
  footer{padding:14px 20px;color:var(--muted);font-size:11.5px;border-top:1px solid var(--line)}
</style>
</head>
<body>
<header>
  <h1>NYC Rent-Stabilized — Likely Sellers</h1>
  <div class="sub">20-50 unit, pre-1974, presumptively rent-stabilized rentals, ranked by <b>refinance / value-loss pressure</b>: a low-rate-era loan estimated to be maturing into a building that has lost value &mdash; so the owner is likely forced to sell. <b>LTV, value change &amp; maturity are estimates</b> &mdash; open the ACRIS mortgage doc to verify before acting.</div>
  <div class="stats" id="stats"></div>
</header>

<div class="controls">
  <input type="text" id="q" placeholder="search address / owner / lender / bbl…" style="min-width:230px">
  <select id="boro"><option value="">All boroughs</option></select>
  <select id="status"><option value="">All statuses</option></select>
  <label><input type="checkbox" id="lev"> refi-hard (est. LTV &ge;90%)</label>
  <label><input type="checkbox" id="mat"> maturing soon</label>
  <label><input type="checkbox" id="dist"> distressed (hazards/lien)</label>
  <label>min pressure <input type="text" id="minscore" value="0" style="width:42px"></label>
  <button class="btn ghost" id="export">Export view CSV</button>
  <span class="muted" id="count"></span>
</div>

<div class="wrap"><table id="t">
  <thead><tr id="hrow"></tr></thead>
  <tbody id="body"></tbody>
</table></div>

<footer id="foot"></footer>

<script>
const DATA = __DATA__;
const LS_KEY = "nyc_targets_v1";
const STATUSES = ["", "Watching", "Contacted", "Diligence", "Offer", "Passed"];
const STATUS_COLOR = {"":"transparent","Watching":"#d29922","Contacted":"#4c9ffe",
  "Diligence":"#a371f7","Offer":"#3fb950","Passed":"#6e7681"};

let saved = {};
try { saved = JSON.parse(localStorage.getItem(LS_KEY)) || {}; } catch(e){}
function persist(){ localStorage.setItem(LS_KEY, JSON.stringify(saved)); }
function getSaved(bbl){ return saved[bbl] || {status:"", notes:""}; }
function setSaved(bbl, patch){ saved[bbl] = Object.assign(getSaved(bbl), patch); persist(); }

// column definitions: key, label, render, sortVal, className
const fmt$ = v => v==null||v===""? "" : "$"+Number(v).toLocaleString();
const fmtM = v => v==null||v===""? "" : v>=1e6? "$"+(v/1e6).toFixed(2)+"M" : "$"+Number(v).toLocaleString();
function fmtMonths(m){
  if(m==null||m==="") return "";
  m=Number(m);
  if(m<0) return Math.abs(m)+" mo ago";
  return "in "+m+" mo";
}
function fmtPct(v){ return v==null||v===""? "" : (Number(v)*100).toFixed(0)+"%"; }
function ltvCell(r){
  if(r.implied_ltv_now==null||r.implied_ltv_now===""){
    return r.loan_blanket==="true"||r.loan_blanket===true||r.loan_blanket==="True"
      ? `<span class="muted" title="loan covers multiple parcels">blanket</span>`
      : `<span class="muted">—</span>`;
  }
  const ltv=Number(r.implied_ltv_now);
  const col = ltv>=1 ? "var(--bad)" : ltv>=0.9 ? "var(--warn)" : "var(--muted)";
  return `<b style="color:${col}">${Math.round(ltv*100)}%</b>`;
}
const COLS = [
  {k:"_status", l:"Status", sort:r=>STATUSES.indexOf(getSaved(r.bbl).status), render:statusCell},
  {k:"score", l:"Sell pressure", cls:"num", sort:r=>r.score, render:r=>{
     const h = 120*(r.score/100); // green→red hue
     return `<span class="score" style="background:hsl(${h},70%,55%)">${r.score}</span>`;}},
  {k:"address", l:"Address", sort:r=>r.address, render:r=>
     `<span class="addr">${esc(r.address)}</span><br><span class="muted">${esc(r.borough)} ${esc(r.zip||"")} · ${r.units}u · ${esc(r.bldg_class)}</span>`},
  {k:"implied_ltv_now", l:"Est. LTV now", cls:"num", sort:r=>r.implied_ltv_now==null||r.implied_ltv_now===""?-1:Number(r.implied_ltv_now), render:ltvCell},
  {k:"value_change_pct", l:"Value vs financing", cls:"num", sort:r=>r.value_change_pct==null||r.value_change_pct===""?9e9:Number(r.value_change_pct), render:r=>{
     if(r.value_change_pct==null||r.value_change_pct==="") return "";
     const v=Number(r.value_change_pct);
     return `<span style="color:${v<0?'var(--bad)':'var(--muted)'}">${v>0?'+':''}${fmtPct(v)}</span>`;}},
  {k:"market_value", l:"Est. value now", cls:"num", sort:r=>r.market_value??0, render:r=>
     `${fmtM(r.market_value)}<br><span class="muted" style="font-size:10.5px">${esc(r.value_basis||"")}</span>`},
  {k:"low_rate_mtge_amt", l:"Loan (low-rate era)", cls:"num", sort:r=>Number(r.low_rate_mtge_amt||0), render:r=>
     r.low_rate_mtge_date? `${fmtM(r.low_rate_mtge_amt)}<br><span class="muted">${r.low_rate_mtge_date}</span>`
       : `<span class="muted">none found</span>`},
  {k:"est_maturity_10yr", l:"Est. maturity (10yr)", sort:r=>r.est_maturity_10yr||"", render:r=>
     r.est_maturity_10yr? `${r.est_maturity_10yr}<br><span class="muted">${fmtMonths(r.months_to_maturity)}</span>` : ""},
  {k:"years_owned", l:"Tenure", cls:"num", sort:r=>r.years_owned===""?-1:Number(r.years_owned), render:r=>
     r.years_owned===""? `<span class="muted">long/held</span>` : `${r.years_owned}y`},
  {k:"_distress", l:"Distress", sort:r=>distressRank(r), render:distressCell},
  {k:"lender", l:"Lender", sort:r=>r.lender||"", render:r=>`<span class="muted">${esc(r.lender||"")}</span>`},
  {k:"owner_contact", l:"Owner contact (HPD)", sort:r=>(r.owner_contact||r.owner||""), render:ownerCell},
  {k:"per_door", l:"$/door", cls:"num", sort:r=>r.per_door??9e15, render:r=>`<span class="muted">${fmt$(r.per_door)}</span>`},
  {k:"flags", l:"Flags", sort:r=>r.flags||"", render:r=>`<div class="flags">${esc(r.flags||"")}</div>`},
  {k:"_links", l:"Verify", sort:r=>0, render:r=>{
     let s=[];
     if(r.acris_mortgage_url) s.push(`<a href="${r.acris_mortgage_url}" target="_blank" rel="noopener">mtge doc</a>`);
     if(r.acris_parcel_url) s.push(`<a href="${r.acris_parcel_url}" target="_blank" rel="noopener">all docs</a>`);
     if(r.pluto_url) s.push(`<a href="${r.pluto_url}" target="_blank" rel="noopener">PLUTO</a>`);
     return s.join(" · ");}},
  {k:"_notes", l:"Notes", sort:r=>getSaved(r.bbl).notes||"", render:notesCell},
];

function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g, c=>(
  {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }

function isLien(r){ return r.on_lien_list===true||r.on_lien_list==="true"||r.on_lien_list==="True"; }
function distressRank(r){ return (isLien(r)?1000:0)+(+r.hazardous_violations||0)*10+(+r.open_violations||0)*0.1; }
function distressCell(r){
  const cc=+r.hazardous_violations||0, op=+r.open_violations||0;
  let s=[];
  if(cc>0) s.push(`<b style="color:var(--bad)">${cc} haz</b>`);
  if(op>0) s.push(`<span class="muted">${op} open</span>`);
  if(isLien(r)) s.push(`<span class="pill" title="${esc(r.lien_cycle)} ${esc(r.lien_month)}" style="background:var(--bad);color:#fff">lien</span>`);
  return s.length? s.join(" ") : `<span class="muted">—</span>`;
}
function ownerCell(r){
  let s=[];
  if(r.owner_contact) s.push(`<b>${esc(r.owner_contact)}</b>`+(r.owner_title?` <span class="muted">(${esc(r.owner_title)})</span>`:""));
  if(r.owner_address) s.push(`<span class="muted">${esc(r.owner_address)}</span>`);
  if(r.managing_agent && r.managing_agent!==r.owner_contact) s.push(`<span class="muted">agent: ${esc(r.managing_agent)}</span>`);
  if(r.owner) s.push(`<span class="muted" style="font-size:10.5px">LLC: ${esc(r.owner)}</span>`);
  return s.length? s.join("<br>") : `<span class="muted">—</span>`;
}

function statusCell(r){
  const cur = getSaved(r.bbl).status;
  const opts = STATUSES.map(s=>`<option value="${s}" ${s===cur?"selected":""}>${s||"New"}</option>`).join("");
  return `<select class="status" data-bbl="${r.bbl}" style="border-left:4px solid ${STATUS_COLOR[cur]}">${opts}</select>`;
}
function notesCell(r){
  return `<input class="notes" data-bbl="${r.bbl}" value="${esc(getSaved(r.bbl).notes)}" placeholder="…">`;
}

let sortKey="score", sortDesc=true;
const RENDER_CAP = 500;   // render only the top N of the filtered+sorted set
const el = id => document.getElementById(id);

function boroList(){
  const set=[...new Set(DATA.map(r=>r.borough))].sort();
  el("boro").insertAdjacentHTML("beforeend",
    set.map(b=>`<option value="${b}">${b}</option>`).join(""));
  el("status").insertAdjacentHTML("beforeend",
    STATUSES.filter(s=>s).concat(["New"]).map(s=>`<option value="${s}">${s}</option>`).join(""));
}

function header(){
  el("hrow").innerHTML = COLS.map(c=>{
    const on = c.k===sortKey;
    return `<th data-k="${c.k}" class="${on?'sorted':''} ${on&&sortDesc?'desc':''}">${c.l}</th>`;
  }).join("");
  el("hrow").querySelectorAll("th").forEach(th=>th.onclick=()=>{
    const k=th.dataset.k;
    if(k===sortKey) sortDesc=!sortDesc; else {sortKey=k; sortDesc=true;}
    render();
  });
}

function currentFilter(){
  const q=el("q").value.trim().toLowerCase();
  const boro=el("boro").value, st=el("status").value;
  const levOnly=el("lev").checked, matOnly=el("mat").checked, distOnly=el("dist").checked;
  const minscore=parseFloat(el("minscore").value)||0;
  return DATA.filter(r=>{
    if(boro && r.borough!==boro) return false;
    if(levOnly && !(r.implied_ltv_now!=null && r.implied_ltv_now!=="" && Number(r.implied_ltv_now)>=0.9)) return false;
    if(matOnly && !r.maturing_soon) return false;
    if(distOnly && !((+r.hazardous_violations||0)>0 || isLien(r))) return false;
    if(r.score<minscore) return false;
    if(st){ const cur=getSaved(r.bbl).status||"New"; if((cur||"New")!==st) return false; }
    if(q){
      const hay=(r.address+" "+r.owner+" "+(r.owner_contact||"")+" "+(r.owner_address||"")+" "+(r.managing_agent||"")+" "+r.lender+" "+r.bbl+" "+r.borough).toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
}

function render(){
  header();
  const col = COLS.find(c=>c.k===sortKey) || COLS[1];
  let rows = currentFilter();
  rows.sort((a,b)=>{
    let x=col.sort(a), y=col.sort(b);
    if(x<y) return sortDesc?1:-1;
    if(x>y) return sortDesc?-1:1;
    return 0;
  });
  const display = rows.slice(0, RENDER_CAP);
  el("body").innerHTML = display.map(r=>"<tr>"+COLS.map(c=>
    `<td class="${c.cls||''}">${c.render(r)}</td>`).join("")+"</tr>").join("");
  el("count").textContent = rows.length > RENDER_CAP
    ? `showing top ${RENDER_CAP} of ${rows.length} matches — refine filters to narrow`
    : rows.length+" matches / "+DATA.length+" total";
  wireRow();
  renderStats(rows);
}

function wireRow(){
  el("body").querySelectorAll("select.status").forEach(s=>s.onchange=e=>{
    setSaved(s.dataset.bbl,{status:e.target.value});
    s.style.borderLeft="4px solid "+STATUS_COLOR[e.target.value];
  });
  el("body").querySelectorAll("input.notes").forEach(n=>n.onchange=e=>{
    setSaved(n.dataset.bbl,{notes:e.target.value});
  });
}

function renderStats(rows){
  const n=DATA.length;
  const under=DATA.filter(r=>r.implied_ltv_now!=null&&r.implied_ltv_now!==""&&Number(r.implied_ltv_now)>=1).length;
  const hard=DATA.filter(r=>r.implied_ltv_now!=null&&r.implied_ltv_now!==""&&Number(r.implied_ltv_now)>=0.9).length;
  const mat=DATA.filter(r=>r.maturing_soon).length;
  const distressed=DATA.filter(r=>(+r.hazardous_violations||0)>0||isLien(r)).length;
  const lien=DATA.filter(r=>isLien(r)).length;
  const watching=Object.values(saved).filter(s=>s.status && s.status!=="Passed").length;
  el("stats").innerHTML=[
    ["Candidates",n],["Est. underwater (LTV≥100%)",under],["Refi-hard (LTV≥90%)",hard],
    ["Maturing soon (est.)",mat],["Distressed (hazards/lien)",distressed],
    ["On tax-lien list",lien],["On your list",watching],
  ].map(([l,v])=>`<div class="stat">${l}<b>${v.toLocaleString()}</b></div>`).join("");
}

function exportCSV(){
  const rows=currentFilter();
  const cols=["score","address","borough","zip","units","implied_ltv_now",
    "value_change_pct","market_value","origination_value","value_basis",
    "low_rate_mtge_date","low_rate_mtge_amt","loan_blanket","est_maturity_10yr",
    "months_to_maturity","maturing_soon","years_owned","open_violations",
    "hazardous_violations","on_lien_list","lien_cycle","owner_contact",
    "owner_title","owner_address","managing_agent","owner","per_door","lender",
    "bbl","flags","acris_mortgage_url"];
  const head=cols.concat(["my_status","my_notes"]);
  const lines=[head.join(",")];
  rows.forEach(r=>{
    const sv=getSaved(r.bbl);
    const vals=cols.map(c=>csv(r[c])).concat([csv(sv.status),csv(sv.notes)]);
    lines.push(vals.join(","));
  });
  const blob=new Blob([lines.join("\n")],{type:"text/csv"});
  const a=document.createElement("a");
  a.href=URL.createObjectURL(blob); a.download="targets_view.csv"; a.click();
}
function csv(v){ v=v==null?"":String(v); return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v; }

["q","boro","status","minscore"].forEach(id=>el(id).oninput=render);
["lev","mat","dist"].forEach(id=>el(id).onchange=render);
el("status").onchange=render;
el("export").onclick=exportCSV;

el("foot").innerHTML="Generated "+new Date().toLocaleString()+
  " · Status &amp; notes are saved in this browser (localStorage) · "+
  "Est. LTV = recorded loan ÷ calibrated current value; maturity = recording date + assumed term. Both are estimates — verify against the ACRIS mortgage doc.";

boroList(); render();
</script>
</body>
</html>
"""
