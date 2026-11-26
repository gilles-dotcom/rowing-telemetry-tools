// Plotly chart: recalculates from current saved interval boundaries on each execution.
{
  let payload=null;
  let problem="Install 04-import-stroke-samples.js in PeachCurves and re-process once.";
  try {
    const raw=icu.activity && icu.activity.PeachCurves;
    let stored=raw && typeof raw === "object" && "value" in raw ? raw.value : raw;
    // Depending on the field version, Intervals may expose Text as a string,
    // an already-decoded value, or a JSON string encoded inside another string.
    for(let pass=0;pass<3 && typeof stored==="string";pass++) {
      let text=stored.trim();
      if(text.startsWith("```")) text=text.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
      if(text.includes("&quot;") || text.includes("&#34;"))
        text=text.replace(/&quot;|&#34;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");
      try { stored=JSON.parse(text); }
      catch(e) {
        // Some field editors prepend a short status line. Recover a complete
        // JSON object when one is present inside that wrapper.
        const first=text.indexOf("{"), last=text.lastIndexOf("}");
        if(first>0 && last>first) {
          try { stored=JSON.parse(text.slice(first,last+1)); continue; } catch(ignore) {}
        }
        const preview=text.length>140 ? text.slice(0,140)+"…" : text;
        throw new Error("PeachCurves is not valid JSON ("+text.length+" characters; starts with "+JSON.stringify(preview)+")");
      }
    }
    // Accept the earlier v1 payload too; it already contains piece averages.
    if(stored && stored.version===1 && Number.isInteger(stored.points) && Array.isArray(stored.pieces)) {
      payload=stored;
    } else if(stored && stored.version===2 && Number.isInteger(stored.points) && stored.points>=2 && stored.points<=127 && Array.isArray(stored.strokes) && stored.strokes.length<=4096 &&
       stored.strokes.every(s=>Number.isFinite(s.t)&&Array.isArray(s.curves)&&s.curves.length===3&&s.curves.every(y=>Array.isArray(y)&&y.length===stored.points&&y.every(v=>v===null||(typeof v==='number'&&Number.isFinite(v)))))) {
      const pieces=[{label:"Whole session · sampled",lo:-Infinity,hi:Infinity}];
      const current=icu.activity.icu_intervals || [];
      if(current.length>200)throw new Error("Too many saved intervals (maximum 200)");
      for(let j=0;j<current.length;j++) {
        const iv=current[j];
        if(Number.isFinite(iv.start_time)&&Number.isFinite(iv.end_time)&&iv.end_time>iv.start_time)
          pieces.push({label:(iv.label || "Piece "+(j+1))+" · "+Math.round(iv.start_time)+"-"+Math.round(iv.end_time)+" s · sampled",lo:iv.start_time,hi:iv.end_time});
      }
      for(const p of pieces) {
        const sums=Array.from({length:3},()=>Array(stored.points).fill(0));
        const counts=Array.from({length:3},()=>Array(stored.points).fill(0));
        p.strokes=0;
        for(const s of stored.strokes) {
          if(s.t<p.lo||s.t>=p.hi)continue;
          p.strokes++;
          for(let k=0;k<3;k++)for(let i=0;i<stored.points;i++) {
            const v=s.curves[k][i];if(v!==null){sums[k][i]+=v;counts[k][i]++;}
          }
        }
        p.curves=sums.map((y,k)=>y.map((v,i)=>counts[k][i]?Math.round(v/counts[k][i]*10)/10:null));
        if(!p.strokes)p.label+=" · no sampled strokes";
      }
      payload={version:1,points:stored.points,pieces};
    }
  }catch(e){problem="Cannot read stroke samples: "+String(e.message || e);}
  if (!payload) {
    chart = {data: [], layout: {
      title: {text: "Peach force curves"}, height: 450,
      xaxis: {visible: false}, yaxis: {visible: false},
      annotations: [{xref: "paper", yref: "paper", x: 0.5, y: 0.5,
        text: problem + "<br>If re-processing reports a memory limit, the importer must be fixed first.",
        showarrow: false, align: "center"}]
    }};
  } else {
    const pieces = payload.pieces;
    const names = ["Port", "Starboard", "Handle"];
    const colors = ["#ef8878", "#00b4d8", "#9696ac"];
    const x = Array.from({length: payload.points}, (_, i) => i * 100 / (payload.points - 1));
    const safe = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const title = p => "Average force · " + safe(p.label) + " · " + p.strokes + " stroke curves";
    // Plotly buttons cannot re-run an Intervals server-side chart script. This
    // link reloads the current activity, which causes the script to read the
    // latest saved intervals without re-processing the FIT file.
    const activityId = icu.activity && icu.activity.id;
    const refreshHref = activityId == null ? "#" : "/activities/" + encodeURIComponent(String(activityId)) + "?peach_refresh=1";
    const refreshText = activityId == null
      ? "Refresh the activity page to read changed pieces"
      : '<a href="' + refreshHref + '">↻ Refresh pieces</a>';
    // Keep the force axis fixed across pieces for fair visual comparisons.
    let peak = 0;
    for (const p of pieces) for (const y of p.curves) for (const v of y)
      if (v !== null && v > peak) peak = v;
    const data = names.map((name, k) => ({
      x, y: pieces[0].curves[k], name, type: "scatter", mode: "lines",
      connectgaps: false, line: {color: colors[k], width: 2.5, dash: k === 2 ? "dot" : "solid"},
      hovertemplate: "%{x:.0f}% · %{y:.1f} N<extra>" + name + "</extra>"
    }));
    chart = {data, layout: {
      title: {text: title(pieces[0]), font: {size: 16}}, height: 500,
      margin: {l: 65, r: 25, t: 120, b: 85}, hovermode: "x unified",
      xaxis: {title: {text: "Recorded sample window (%)"}, range: [0, 100]},
      yaxis: {title: {text: "Force (N)"}, range: [0, Math.max(100, Math.ceil(peak * 1.1 / 50) * 50)]},
      legend: {orientation: "h", x: 0, y: -0.2},
      annotations: [{xref: "paper", yref: "paper", x: 1, y: 1.22,
        xanchor: "right", yanchor: "middle", showarrow: false,
        captureevents: true, text: refreshText,
        font: {size: 13, color: "#1683d8"}}],
      updatemenus: [{type: "dropdown", direction: "down", x: 0, xanchor: "left", y: 1.22,
        buttons: pieces.map(p => ({label: safe(p.label) + " (" + p.strokes + ")", method: "update",
          args: [{y: p.curves}, {title: {text: title(p)}}]}))}]
    }};
  }
}
