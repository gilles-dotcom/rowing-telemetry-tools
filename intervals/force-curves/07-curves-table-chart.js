// Peach force curves + piece table. Custom activity chart (Plotly).
// Data:   PeachData1..16 (08: curves + metrics of every stroke). Legacy fallback:
//         PeachCurves (04) / PeachCurves1..8 and PeachStrokes1..8 (06).
// Table:  Intervals' own interval numbers (power, rate, split, HR, distance) plus
//         the per-stroke rowing metrics. Both follow the CURRENT saved
//         intervals each time the chart runs.
{
  const HEIGHT = 560;      // your preferred chart height (px)
  const ROW_PX = 21;       // table row height (px)
  const MAX_FIELDS = 16;

  const act = icu.activity || {};
  const unwrap = r => (r && typeof r === "object" && "value" in r ? r.value : r);
  const safe = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const fin = v => typeof v === "number" && Number.isFinite(v);
  const clock = s => { if (!fin(s)) return "-"; s = Math.max(0, Math.round(s));
    const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0"); };
  const split = v => fin(v) && v > 0.3 ? (() => { const s = 500 / v, m = Math.floor(s / 60);
    return m + ":" + (s - m * 60).toFixed(1).padStart(4, "0"); })() : "-";
  const num = (v, d = 0) => (fin(v) ? v.toFixed(d) : "-");
  const notes = [];
  const pick = (...v) => { for (const x of v) if (fin(x)) return x; return null; };

  // ---------- field diagnostics
  const diag = [];
  const describe = code => { const r = unwrap(act[code]);
    if (r == null || r === "") return null;
    const s = typeof r === "string" ? r : JSON.stringify(r);
    return s.startsWith("PROBE|") && s.length < 400 ? s.slice(6) : s.slice(0, 12) + "… " + s.length + " chars"; };
  for (const code of ["PeachProbe1", "PeachProbe2", "PeachProbe3", "PeachProbe4"]) {
    const d = describe(code);
    if (d) diag.push(code + ": " + d);
  }

  const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_", D = {};
  for (let i = 0; i < 64; i++) D[B64[i]] = i;
  const dec = (s, i) => D[s[i]] * 64 + D[s[i + 1]];

  // ---------- PeachData (08): curves + metrics per stroke
  let N = 0;
  const curves = [], strokes = [];
  const PD_COLS = [["StrokeDriveTime", .1, 0], ["StrokeRecoveryTime", .1, 0],
    ["AverageDriveForce", 1, 0], ["PeakDriveForce", 1, 0], ["StrokeWork", 1, 0],
    ["CatchPort", 10, 150], ["CatchStarboard", 10, 150], ["FinishPort", 10, 150], ["FinishStarboard", 10, 150],
    ["SlipPort", 10, 150], ["SlipStarboard", 10, 150], ["WashPort", 10, 150], ["WashStarboard", 10, 150],
    ["EffectiveArcPort", 10, 150], ["EffectiveArcStarboard", 10, 150],
    ["PeakForceAnglePort", 10, 150], ["PeakForceAngleStarboard", 10, 150]];
  let pdShards = 0;
  const pdSeen = {};
  for (let k = 1; k <= MAX_FIELDS; k++) {
    const raw = unwrap(act["PeachData" + k]);
    if (typeof raw !== "string" || !raw.startsWith("PD1|")) {
      if (raw != null && raw !== "") diag.push("PeachData" + k + ": unreadable (" + String(raw).slice(0, 20) + ")");
      continue;
    }
    const parts = raw.split("|"), sh = parts[1].split("/").map(Number), n = Number(parts[2]);
    pdShards = Math.max(pdShards, sh[1]); pdSeen[sh[0]] = (pdSeen[sh[0]] || 0) + 1; N = n;
    let got = 0;
    for (const rec of (parts[4] || "").split(";")) {
      const c = rec.indexOf(":"), t = Number(rec.slice(0, c)), s = rec.slice(c + 1);
      if (!fin(t) || s.length !== 2 * PD_COLS.length + 4 * n) continue;
      got++;
      const o = { t };
      PD_COLS.forEach((col, j) => { const v = dec(s, 2 * j); o[col[0]] = v === 4095 ? null : v / col[1] - col[2]; });
      strokes.push(o);
      const b0 = 2 * PD_COLS.length, p = [], q = [], h = [];
      let any = false;
      for (let j = 0; j < n; j++) {
        const a = dec(s, b0 + 2 * j), b = dec(s, b0 + 2 * (n + j));
        p.push(a === 4095 ? null : a / 2); q.push(b === 4095 ? null : b / 2);
        h.push(a === 4095 || b === 4095 ? null : (a + b) / 4);
        if (a !== 4095 || b !== 4095) any = true;
      }
      if (any) curves.push({ t, y: [p, q, h] });
    }
    diag.push("PeachData" + k + " (" + parts[1] + "): " + got + " strokes, " + raw.length + " chars" +
      (got !== Number(parts[3]) ? " · expected " + parts[3] + " (cut off?)" : ""));
  }
  if (pdShards) {
    const missing = [];
    for (let k = 1; k <= pdShards; k++) if (!pdSeen[k]) missing.push(k);
    if (missing.length) notes.push("PeachData missing shard " + missing.join(", ") + " of " + pdShards);
  }

  // ---------- legacy curves (per stroke: t + port/stbd/handle arrays of N points)
  const v2 = curves.length ? null : unwrap(act.PeachCurves);
  if (typeof v2 === "string" && v2.trim().startsWith("{")) {
    try {
      const o = JSON.parse(v2);
      if (o.version === 2 && Array.isArray(o.strokes)) {
        N = o.points;
        for (const s of o.strokes) if (fin(s.t) && s.curves && s.curves.length === 3) curves.push({ t: s.t, y: s.curves });
      } else notes.push("PeachCurves is version " + o.version + " (needs 04 for version 2)");
    } catch (e) { notes.push("PeachCurves is not valid JSON"); }
  }
  for (let k = 1; k <= MAX_FIELDS && !curves.length; k++) {
    const raw = unwrap(act["PeachCurves" + k]);
    if (typeof raw !== "string" || !raw.startsWith("PC3|")) continue;
    const parts = raw.split("|"), n = Number(parts[2]);
    if (!parts[4]) continue;
    N = n;
    for (const rec of parts[4].split(";")) {
      const c = rec.indexOf(":"), t = Number(rec.slice(0, c)), s = rec.slice(c + 1);
      if (!fin(t) || s.length !== 4 * n) continue;
      const p = [], q = [], h = [];
      for (let j = 0; j < n; j++) {
        const a = D[s[2 * j]] * 64 + D[s[2 * j + 1]], b = D[s[2 * (n + j)]] * 64 + D[s[2 * (n + j) + 1]];
        p.push(a === 4095 ? null : a / 2); q.push(b === 4095 ? null : b / 2);
        h.push(a === 4095 || b === 4095 ? null : (a + b) / 4);
      }
      curves.push({ t, y: [p, q, h] });
    }
  }
  curves.sort((a, b) => a.t - b.t);

  // ---------- legacy per-stroke metrics
  let cols = null;
  for (let k = 1; k <= MAX_FIELDS && !pdShards; k++) {
    const raw = unwrap(act["PeachStrokes" + k]);
    if (typeof raw !== "string" || !raw.startsWith("PS1|")) continue;
    const parts = raw.split("|");
    cols = parts[2].split(",");
    if (!parts[4]) continue;
    for (const r of parts[4].split(";")) {
      const v = r.split(","), o = {};
      for (let j = 0; j < cols.length; j++) o[cols[j]] = v[j] === "" || v[j] == null ? null : Number(v[j]);
      if (fin(o.t)) strokes.push(o);
    }
  }
  strokes.sort((a, b) => a.t - b.t);
  if (!strokes.length) notes.push("No stroke metrics: add PeachData fields (08) and re-process");

  // ---------- pieces = whole session + current saved intervals
  const pieces = [{ label: "Whole session", from: -1e9, to: 1e9, src: act, whole: true }];
  (act.icu_intervals || []).slice(0, 200).forEach((iv, j) => {
    if (fin(iv.start_time) && fin(iv.end_time) && iv.end_time > iv.start_time)
      pieces.push({ label: (iv.label || "Interval " + (j + 1)) + " · " + clock(iv.start_time) + "-" + clock(iv.end_time),
        from: iv.start_time, to: iv.end_time, src: iv });
  });

  const inRange = (arr, p) => arr.filter(s => s.t >= p.from && s.t < p.to);
  const mean = (rows, key) => { let s = 0, n = 0; for (const r of rows) if (fin(r[key])) { s += r[key]; n++; } return n ? s / n : null; };
  const r1 = v => (v === null ? null : Math.round(v * 10) / 10);
  const xs = N ? Array.from({ length: N }, (_, i) => Math.round(i * 1000 / (N - 1)) / 10) : [];

  for (const p of pieces) {
    // curve averages
    const cs = inRange(curves, p);
    p.nCurves = cs.length;
    p.c = [0, 1, 2].map(k => Array.from({ length: N }, (_, i) => {
      let s = 0, n = 0; for (const c of cs) { const v = c.y[k][i]; if (v !== null && fin(v)) { s += v; n++; } }
      return n ? r1(s / n) : null; }));
    p.peak = [0, 1].map(k => { let bi = -1; p.c[k].forEach((v, i) => { if (v !== null && (bi < 0 || v > p.c[k][bi])) bi = i; });
      return bi < 0 ? { x: [], y: [], text: [] } : { x: [xs[bi]], y: [p.c[k][bi]], text: [Math.round(p.c[k][bi]) + " N"] }; });

    // table
    const st = inRange(strokes, p), g = k => mean(st, k), src = p.src;
    const dur = p.whole ? pick(src.elapsed_time, src.moving_time) : pick(src.elapsed_time, p.to - p.from);
    const watts = pick(src.icu_average_watts, src.average_watts, g("power"));
    const rate = pick(src.average_cadence, g("cadence256"));
    const speed = pick(src.average_speed, g("enhanced_speed"));
    const drive = g("StrokeDriveTime"), rec = g("StrokeRecoveryTime");
    const expected = !p.whole && fin(rate) && fin(dur) && rate > 5 ? Math.round(rate * dur / 60) : 0;
    const pkP = p.peak[0].y[0], pkS = p.peak[1].y[0];
    const side = (label, a, d = 1) => {
      const P = g(a + "Port"), S = g(a + "Starboard");
      return [label, num(P, d), num(S, d), fin(P) && fin(S) ? (S - P >= 0 ? "+" : "") + (S - P).toFixed(d) : "-",
        fin(P) && fin(S) ? ((P + S) / 2).toFixed(d) : "-"];
    };
    const catchP = g("CatchPort"), catchS = g("CatchStarboard"), finP = g("FinishPort"), finS = g("FinishStarboard");
    const arcP = fin(catchP) && fin(finP) ? finP - catchP : null, arcS = fin(catchS) && fin(finS) ? finS - catchS : null;
    p.rows = [
      ["Duration", "", "", "", clock(dur)],
      ["Distance (m)", "", "", "", num(src.distance)],
      ["Power (W)", "", "", "", num(watts)],
      ["Rate (spm)", "", "", "", num(rate, 1)],
      ["Split /500m", "", "", "", split(speed)],
      ["Heart rate", "", "", "", num(src.average_heartrate)],
      ["Strokes found / expected", "", "", "", expected ? (st.length < 0.9 * expected ? "⚠ " : "") + st.length + " / " + expected : String(st.length)],
      ["Drive / recovery (s)", "", "", "", fin(drive) && fin(rec) ? (drive / 1000).toFixed(2) + " / " + (rec / 1000).toFixed(2) : "-"],
      ["Drive % of stroke", "", "", "", fin(drive) && fin(rec) ? (100 * drive / (drive + rec)).toFixed(0) + "%" : "-"],
      ["Avg force (N)", "", "", "", num(g("AverageDriveForce"))],
      ["Peak force (N)", "", "", "", num(g("PeakDriveForce"))],
      ["Peak of avg curve (N)", num(pkP), num(pkS), fin(pkP) && fin(pkS) ? (pkS - pkP >= 0 ? "+" : "") + (pkS - pkP).toFixed(0) : "-",
        fin(pkP) && fin(pkS) ? ((pkP + pkS) / 2).toFixed(0) : "-"],
      ["Work / stroke (J)", "", "", "", num(g("StrokeWork"))],
      side("Catch (°)", "Catch"),
      side("Finish (°)", "Finish"),
      ["Total arc (°)", num(arcP, 1), num(arcS, 1), fin(arcP) && fin(arcS) ? (arcS - arcP >= 0 ? "+" : "") + (arcS - arcP).toFixed(1) : "-",
        fin(arcP) && fin(arcS) ? ((arcP + arcS) / 2).toFixed(1) : "-"],
      side("Slip (°)", "Slip"),
      side("Wash (°)", "Wash"),
      side("Effective arc (°)", "EffectiveArc"),
      side("Peak force angle (°)", "PeakForceAngle"),
    ];
  }

  // ---------- layout
  const TX0 = 0.6;                                   // table starts here (paper x)
  const colX = [TX0, 0.79, 0.875, 0.94, 1.0];        // label | Port | Stbd | Δ S-P | Boat
  const head = ["", "Port", "Stbd", "Δ S-P", "Boat"];
  const table = p => {
    const a = [];
    const cell = (text, c, r, bold) => a.push({ xref: "paper", yref: "paper", x: colX[c], y: 1, yshift: -r * ROW_PX - ROW_PX / 2,
      xanchor: c ? "right" : "left", yanchor: "middle", showarrow: false,
      text: bold ? "<b>" + text + "</b>" : text, font: { size: 12, color: c && !bold ? "#222" : "#555" } });
    head.forEach((h, c) => cell(h, c, 0, true));
    p.rows.forEach((row, r) => row.forEach((v, c) => cell(c ? v : safe(v), c, r + 1, c === 4 && r < 7)));
    if (diag.length) a.push({ xref: "paper", yref: "paper", x: 0, y: 0, yshift: -48, xanchor: "left", yanchor: "top",
      showarrow: false, align: "left", font: { size: 10, color: "#777" }, text: safe(diag.join("\n")).replace(/\n/g, "<br>") });
    if (notes.length) a.push({ xref: "paper", yref: "paper", x: TX0, y: 1, yshift: -(p.rows.length + 1.6) * ROW_PX,
      xanchor: "left", yanchor: "top", showarrow: false, align: "left", font: { size: 10, color: "#a33" }, text: safe(notes.join(" · ")) });
    return a;
  };
  const shapes = [];
  for (let r = 0; r <= pieces[0].rows.length; r += 2)
    shapes.push({ type: "rect", xref: "paper", yref: "paper", x0: TX0 - 0.01, x1: 1.005, ysizemode: "pixel", yanchor: 1,
      y0: -r * ROW_PX, y1: -(r + 1) * ROW_PX, line: { width: 0 }, fillcolor: r ? "rgba(0,180,216,0.06)" : "rgba(15,28,46,0.07)", layer: "below" });
  [7, 13].forEach(r => shapes.push({ type: "line", xref: "paper", yref: "paper", x0: TX0 - 0.01, x1: 1.005, ysizemode: "pixel", yanchor: 1,
    y0: -(r + 1) * ROW_PX, y1: -(r + 1) * ROW_PX, line: { width: 1, color: "rgba(15,28,46,0.25)" } }));

  let ymax = 0;
  for (const p of pieces) for (const y of p.c) for (const v of y) if (v !== null && v > ymax) ymax = v;
  const computed = new Date().toISOString().slice(11, 16) + " UTC";
  const title = p => "<b>" + safe(p.label) + "</b><br><span style='font-size:11px;color:#777'>Curves: " + p.nCurves +
    " stroke" + (p.nCurves === 1 ? "" : "s") + (curves.length < strokes.length ? " (sampled)" : "") + " · computed " + computed + "</span>";
  const traceArgs = p => ({ x: [xs, xs, xs, p.peak[0].x, p.peak[1].x], y: [p.c[0], p.c[1], p.c[2], p.peak[0].y, p.peak[1].y],
    text: [null, null, null, p.peak[0].text, p.peak[1].text] });
  const P0 = pieces[0], A0 = traceArgs(P0);
  const C = { port: "#e4572e", stbd: "#00a6cc", handle: "#0f1c2e" };
  const line = (k, name, color, extra) => Object.assign({ x: A0.x[k], y: A0.y[k], name, type: "scatter", mode: "lines",
    line: { color, width: 3 }, hovertemplate: "%{y:.0f} N<extra>" + name + "</extra>" }, extra);
  const dot = (k, color, pos) => ({ x: A0.x[k], y: A0.y[k], text: A0.text[k], type: "scatter", mode: "markers+text",
    marker: { color, size: 9, line: { color: "#fff", width: 1.5 } }, textposition: pos, textfont: { color, size: 12 },
    showlegend: false, hoverinfo: "skip" });

  chart = N ? {
    data: [
      line(0, "Port", C.port),
      line(1, "Starboard", C.stbd, { fill: "tonexty", fillcolor: "rgba(120,120,140,0.13)" }),
      line(2, "Handle (mean)", C.handle, { line: { color: C.handle, width: 1.5, dash: "dot" } }),
      dot(3, C.port, "top left"), dot(4, C.stbd, "top right"),
    ],
    layout: {
      height: HEIGHT + 13 * diag.length, margin: { l: 55, r: 15, t: 95, b: 60 + 13 * diag.length },
      title: { text: title(P0), x: 0, xanchor: "left", y: 0.97, font: { size: 15, color: "#0f1c2e" } },
      hovermode: "x unified", plot_bgcolor: "#fff",
      xaxis: { domain: [0, TX0 - 0.06], range: [0, 100], title: { text: "Drive: catch → finish (%)", font: { size: 12 } },
        gridcolor: "#eee", zeroline: false, ticksuffix: "%" },
      yaxis: { range: [0, Math.max(100, Math.ceil(ymax * 1.12 / 50) * 50)], title: { text: "Force (N)", font: { size: 12 } },
        gridcolor: "#eee", zeroline: false },
      legend: { orientation: "h", traceorder: "normal", x: 0, xanchor: "left", y: 1.0, yanchor: "bottom", font: { size: 12 } },
      updatemenus: [{ type: "dropdown", direction: "down", x: 1, xanchor: "right", y: 1, yanchor: "bottom", pad: { b: 30 },
        bgcolor: "#f4f8fb", bordercolor: "#c9d6e0", font: { size: 12 },
        buttons: pieces.map(p => ({ label: safe(p.label) + "  (" + p.nCurves + ")", method: "update",
          args: [traceArgs(p), { "title.text": title(p), annotations: table(p) }] })) }],
      annotations: table(P0), shapes
    }
  } : {
    data: [], layout: { height: 300, xaxis: { visible: false }, yaxis: { visible: false },
      annotations: [{ xref: "paper", yref: "paper", x: 0.5, y: 0.5, showarrow: false,
        text: "No Peach curve data. Install 04 in PeachCurves and re-process." + (notes.length ? "<br>" + safe(notes.join(" · ")) : "") }] }
  };
}
