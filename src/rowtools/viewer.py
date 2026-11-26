"""One self-contained HTML page that overlays FIT files. No network, no uploads."""
from __future__ import annotations

import json
from pathlib import Path

FIELDS = {"power": "Power (W)", "hr": "Heart rate (bpm)", "rate": "Rate (spm)", "pace": "Pace (s/500 m)"}

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>FIT overlay</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><style>
:root{--bg:#fff;--fg:#1d2327;--mut:#6b757c;--grid:#e3e7ea}
@media(prefers-color-scheme:dark){:root{--bg:#14181b;--fg:#e8ecee;--mut:#98a3aa;--grid:#2a3135}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
header{padding:12px 16px}h1{font-size:17px;margin:0}.row{display:flex;flex-wrap:wrap;gap:6px 18px;padding:2px 16px;align-items:center}
input[type=number]{width:76px}canvas{width:100%;display:block;cursor:crosshair}.sw{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px}
#tip{padding:4px 16px;color:var(--mut);min-height:1.5em;font-variant-numeric:tabular-nums}button,input{background:var(--bg);color:var(--fg);border:1px solid var(--grid);border-radius:4px;padding:2px 6px}
</style></head><body><header><h1>FIT overlay</h1></header><div id="files"></div><div class="row" id="fields"></div>
<div class="row"><button id="reset">Reset zoom</button><span style="color:var(--mut)">Drag to zoom. Shift a file with its offset in seconds. Points are the file's own records, joined for display; a break over 15 s is left open.</span></div>
<div id="tip"></div><canvas id="c"></canvas><script>
const D=__DATA__,F=__FIELDS__,COL=["#2a6fdb","#d8452b","#2a9d6f","#8a5cd6","#c08a00","#0aa0b5"];
const on={power:1,hr:1,rate:0,pace:0},off=D.map(()=>0),show=D.map(()=>1);let lo,hi,drag=null;
const c=document.getElementById("c"),x=c.getContext("2d"),t0=Math.min(...D.map(d=>d.start));
const T=(i,k)=>D[i].start-t0+D[i].t[k]+off[i];
function full(){lo=Infinity;hi=-Infinity;D.forEach((d,i)=>{lo=Math.min(lo,T(i,0));hi=Math.max(hi,T(i,d.t.length-1))})}
const mk=(t,p)=>{const e=document.createElement(t);p.appendChild(e);return e};
D.forEach((d,i)=>{const r=mk("div",document.getElementById("files"));r.className="row";const l=mk("label",r),b=mk("input",l);b.type="checkbox";b.checked=true;b.onchange=()=>{show[i]=b.checked;draw()};
const s=mk("span",l);s.className="sw";s.style.background=COL[i%COL.length];mk("span",l).textContent=d.name;const n=mk("input",mk("label",r));n.type="number";n.value=0;n.title="offset (s)";n.oninput=()=>{off[i]=+n.value||0;draw()}});
Object.keys(F).forEach(k=>{const l=mk("label",document.getElementById("fields")),b=mk("input",l);b.type="checkbox";b.checked=!!on[k];b.onchange=()=>{on[k]=b.checked;draw()};mk("span",l).textContent=" "+F[k]});
function draw(){const w=c.clientWidth,ks=Object.keys(F).filter(k=>on[k]&&D.some(d=>d[k])),H=190,r=devicePixelRatio||1,h=Math.max(1,ks.length)*H+24;c.width=w*r;c.height=h*r;c.style.height=h+"px";x.setTransform(r,0,0,r,0,0);
const st=getComputedStyle(document.body),grid=st.getPropertyValue("--grid"),mut=st.getPropertyValue("--mut"),L=48,X=t=>L+(t-lo)/(hi-lo)*(w-L-12);x.clearRect(0,0,w,h);x.font="11px system-ui";
ks.forEach((k,p)=>{let a=Infinity,b=-Infinity;D.forEach((d,i)=>{if(show[i]&&d[k])d[k].forEach((v,j)=>{if(v!=null&&T(i,j)>=lo&&T(i,j)<=hi){a=Math.min(a,v);b=Math.max(b,v)}})});if(a===Infinity)return;if(k=="power")a=0;if(k=="pace"){b=Math.min(b,a+90)}if(b<=a)b=a+1;
const y0=p*H+18,y1=(p+1)*H-8,Y=v=>k=="pace"?y0+(v-a)/(b-a)*(y1-y0):y1-(v-a)/(b-a)*(y1-y0);x.strokeStyle=grid;x.fillStyle=mut;for(let g=0;g<=4;g++){const v=a+(b-a)*g/4;x.beginPath();x.moveTo(L,Y(v));x.lineTo(w-12,Y(v));x.stroke();x.fillText(Math.round(v),8,Y(v)+4)}x.fillText(F[k],L,y0-5);
x.save();x.beginPath();x.rect(L,y0-2,w-L-12,y1-y0+4);x.clip();D.forEach((d,i)=>{if(!show[i]||!d[k])return;x.strokeStyle=COL[i%COL.length];x.lineWidth=1.3;x.beginPath();let pt=null;d[k].forEach((v,j)=>{const t=T(i,j);if(v==null){return}if(pt==null||t-pt>15)x.moveTo(X(t),Y(v));else x.lineTo(X(t),Y(v));pt=t});x.stroke()});x.restore()});
x.fillStyle=mut;for(let g=0;g<=6;g++){const t=lo+(hi-lo)*g/6;x.fillText(fmt(t),X(t)-14,h-6)}if(drag&&drag.b!=null){x.fillStyle="rgba(128,128,128,.2)";x.fillRect(drag.a,0,drag.b-drag.a,h)}}
const fmt=t=>{t=Math.round(t);const s=t<0?"-":"";t=Math.abs(t);return s+Math.floor(t/60)+":"+String(t%60).padStart(2,"0")};
const at=e=>e.clientX-c.getBoundingClientRect().left,TT=p=>lo+(p-48)/(c.clientWidth-60)*(hi-lo);
c.onmousedown=e=>{drag={a:at(e),b:null}};c.onmousemove=e=>{if(drag){drag.b=at(e);draw();return}const t=TT(at(e));document.getElementById("tip").textContent=fmt(t)+"   "+D.map((d,i)=>{if(!show[i])return"";let j=0,m=1e9;for(let k=0;k<d.t.length;k++){const q=Math.abs(T(i,k)-t);if(q<m){m=q;j=k}}return m>15?"":d.name+": "+Object.keys(F).filter(k=>d[k]&&d[k][j]!=null).map(k=>F[k].split(" (")[0]+" "+Math.round(d[k][j])).join(", ")}).filter(Boolean).join("   |   ")};
window.onmouseup=()=>{if(drag&&drag.b!=null&&Math.abs(drag.b-drag.a)>6){const a=TT(Math.min(drag.a,drag.b)),b=TT(Math.max(drag.a,drag.b));lo=a;hi=b}drag=null;draw()};
document.getElementById("reset").onclick=()=>{full();draw()};window.onresize=draw;full();draw();
</script></body></html>"""


def _load(path: str | Path) -> dict:
    import fitparse
    from datetime import timezone

    t, cols = [], {k: [] for k in FIELDS}
    for m in fitparse.FitFile(str(path)).get_messages("record"):
        v = m.get_values()
        if v.get("timestamp") is None:
            continue
        sp = v.get("enhanced_speed", v.get("speed"))
        t.append(v["timestamp"].replace(tzinfo=timezone.utc).timestamp())
        cols["power"].append(v.get("power"))
        cols["hr"].append(v.get("heart_rate"))
        cols["rate"].append(v.get("cadence"))
        cols["pace"].append(round(500.0 / sp, 1) if sp and sp > 1.0 else None)
    if not t:
        raise ValueError(f"no records in {Path(path).name}")
    d = {"name": Path(path).stem, "start": t[0], "t": [x - t[0] for x in t]}
    d.update({k: c for k, c in cols.items() if any(v is not None for v in c)})
    return d


def write_overlay(files, out: str | Path) -> Path:
    data = json.dumps([_load(f) for f in files]).replace("</", "<\\/")
    out = Path(out)
    out.write_text(_PAGE.replace("__DATA__", data).replace("__FIELDS__", json.dumps(FIELDS)), encoding="utf-8")
    return out
