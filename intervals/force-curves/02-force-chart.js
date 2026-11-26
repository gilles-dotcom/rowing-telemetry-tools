// Plotly activity chart. Reads the Text activity field PeachCurves from 01-import-curves.js.
{
  const raw = icu.activity.PeachCurves;
  let payload = null;
  let problem = "PeachCurves is empty. Run the 01 importer with FIT processing enabled.";
  if (raw) {
    try {
      payload = JSON.parse(raw);
      if (payload.version !== 1 || !Number.isInteger(payload.points) || payload.points < 2 ||
          !Array.isArray(payload.pieces) || !payload.pieces.length ||
          !payload.pieces.every(p => typeof p.label === "string" && Number.isFinite(p.strokes) &&
            Array.isArray(p.curves) && p.curves.length === 3 &&
            p.curves.every(y => Array.isArray(y) && y.length === payload.points &&
              y.every(v => v === null || (typeof v === "number" && Number.isFinite(v)))))) {
        payload = null;
        problem = "PeachCurves has an incompatible format. Re-run the 01 importer.";
      }
    } catch (e) {
      payload = null;
      problem = "PeachCurves is not valid JSON. Restore the 01 importer if the diagnostic script is installed.";
    }
  }
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
      updatemenus: [{type: "dropdown", direction: "down", x: 0, xanchor: "left", y: 1.22,
        buttons: pieces.map(p => ({label: safe(p.label) + " (" + p.strokes + ")", method: "update",
          args: [{y: p.curves}, {title: {text: title(p)}}]}))}]
    }};
  }
}
