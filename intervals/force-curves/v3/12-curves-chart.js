// Peach force curves chart (v3). Custom activity chart (Plotly).
// Reads PeachCurves1..PeachCurves16 (written by 11-import-shard.js) and averages
// them over the activity's CURRENT saved intervals each time the chart runs.
// A slider also steps a fixed window through the whole session.
{
  const WINDOW_S = 60, STEP_S = 30, MAX_FIELDS = 16;
  const A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  const D = {}; for (let i = 0; i < 64; i++) D[A[i]] = i;
  const act = icu.activity || {};
  const strokes = [], notes = [];
  let N = 0, shards = 0;
  const seen = {};
  for (let k = 1; k <= MAX_FIELDS; k++) {
    let raw = act["PeachCurves" + k];
    if (raw && typeof raw === "object" && "value" in raw) raw = raw.value;
    if (typeof raw !== "string" || !raw.length) continue;
    const parts = raw.split("|");
    if (parts[0] !== "PC3" || parts.length < 5) { notes.push("PeachCurves" + k + ": not v3 data (starts " + JSON.stringify(raw.slice(0, 30)) + ")"); continue; }
    const sh = parts[1].split("/").map(Number), n = Number(parts[2]);
    if (N && n !== N) { notes.push("PeachCurves" + k + ": point count differs, skipped"); continue; }
    N = n; shards = Math.max(shards, sh[1]); seen[sh[0]] = true;
    const body = parts.slice(4).join("|");
    if (!body) continue;
    for (const rec of body.split(";")) {
      const c = rec.indexOf(":"), t = Number(rec.slice(0, c)), s = rec.slice(c + 1);
      if (!Number.isFinite(t) || s.length !== 4 * N) continue;
      const y = new Array(2 * N);
      for (let j = 0; j < 2 * N; j++) {
        const v = D[s[2 * j]] * 64 + D[s[2 * j + 1]];
        y[j] = v === 4095 ? null : v / 2;
      }
      strokes.push({ t, y });
    }
  }
  strokes.sort((a, b) => a.t - b.t);
  const loaded = Object.keys(seen).length;
  if (!strokes.length) {
    chart = { data: [], layout: { height: 420, xaxis: { visible: false }, yaxis: { visible: false },
      annotations: [{ xref: "paper", yref: "paper", x: .5, y: .5, showarrow: false, align: "center",
        text: "No Peach curve data yet.<br>Create fields PeachCurves1..K with 11-import-shard.js and re-process the FIT." +
              (notes.length ? "<br>" + notes.join("<br>") : "") }] } };
  } else {
    const lower = x => { let lo = 0, hi = strokes.length; while (lo < hi) { const m = (lo + hi) >> 1; strokes[m].t < x ? lo = m + 1 : hi = m; } return lo; };
    const r1 = v => v === null ? null : Math.round(v * 10) / 10;
    const average = (from, to) => {           // [from, to) seconds since start
      const sum = new Array(3 * N).fill(0), cnt = new Array(3 * N).fill(0);
      let n = 0;
      for (let i = lower(from); i < strokes.length && strokes[i].t < to; i++) {
        const y = strokes[i].y; n++;
        for (let j = 0; j < N; j++) {
          const p = y[j], s = y[N + j];
          if (p !== null) { sum[j] += p; cnt[j]++; }
          if (s !== null) { sum[N + j] += s; cnt[N + j]++; }
          if (p !== null && s !== null) { sum[2 * N + j] += (p + s) / 2; cnt[2 * N + j]++; }
        }
      }
      const c = [0, 1, 2].map(k => Array.from({ length: N }, (_, j) => cnt[k * N + j] ? r1(sum[k * N + j] / cnt[k * N + j]) : null));
      return { n, c };
    };
    const fmt = s => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
      return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0"); };
    const safe = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

    const pieces = [{ label: "Whole session", from: -1e9, to: 1e9 }];
    const ivs = act.icu_intervals || [];
    for (let j = 0; j < ivs.length && j < 200; j++) {
      const iv = ivs[j];
      if (!(Number.isFinite(iv.start_time) && Number.isFinite(iv.end_time) && iv.end_time > iv.start_time)) continue;
      pieces.push({ label: (iv.label || "Interval " + (j + 1)) + " · " + fmt(iv.start_time) + "-" + fmt(iv.end_time), from: iv.start_time, to: iv.end_time });
    }
    for (const p of pieces) Object.assign(p, average(p.from, p.to));

    const t0 = Math.floor(strokes[0].t / STEP_S) * STEP_S, tEnd = strokes[strokes.length - 1].t;
    const windows = [];
    for (let w = t0; w <= tEnd; w += STEP_S) { const a = average(w, w + WINDOW_S); if (a.n) windows.push(Object.assign({ from: w, to: w + WINDOW_S }, a)); }

    let peak = 0;
    for (const p of pieces.concat(windows)) for (const y of p.c) for (const v of y) if (v !== null && v > peak) peak = v;
    const x = Array.from({ length: N }, (_, i) => Math.round(i * 1000 / (N - 1)) / 10);
    const names = ["Port", "Starboard", "Handle (mean)"], colors = ["#ef8878", "#00b4d8", "#9696ac"];
    const cov = loaded + "/" + shards + " shards";
    const title = (lab, n) => "Average force · " + safe(lab) + " · " + n + " strokes";
    chart = {
      data: names.map((name, k) => ({ x, y: pieces[0].c[k], name, type: "scatter", mode: "lines",
        line: { color: colors[k], width: k === 2 ? 2 : 2.5, dash: k === 2 ? "dot" : "solid" },
        hovertemplate: "%{x:.0f}% · %{y:.0f} N<extra>" + name + "</extra>" })),
      layout: {
        title: { text: title(pieces[0].label, pieces[0].n), font: { size: 15 } }, height: 560,
        margin: { l: 60, r: 20, t: 110, b: 195 }, hovermode: "x unified",
        xaxis: { title: { text: "Drive (catch → finish, % of samples)" }, range: [0, 100] },
        yaxis: { title: { text: "Force (N)" }, range: [0, Math.max(100, Math.ceil(peak * 1.08 / 50) * 50)] },
        legend: { orientation: "h", x: 0, y: 1.12 },
        updatemenus: [{ type: "dropdown", x: 1, xanchor: "right", y: 1.22, yanchor: "top",
          buttons: pieces.map(p => ({ label: safe(p.label) + " (" + p.n + ")", method: "update",
            args: [{ y: p.c }, { title: { text: title(p.label, p.n), font: { size: 15 } } }] })) }],
        sliders: windows.length ? [{ active: 0, y: -0.18, len: 1, pad: { t: 30 },
          currentvalue: { prefix: "Slide: " + WINDOW_S + " s window from ", font: { size: 12 } },
          steps: windows.map(w => ({ label: fmt(w.from), method: "update",
            args: [{ y: w.c }, { title: { text: title(fmt(w.from) + "-" + fmt(w.to), w.n), font: { size: 15 } } }] })) }] : [],
        annotations: [{ xref: "paper", yref: "paper", x: 0, y: 0, yshift: -175, xanchor: "left", yanchor: "top", showarrow: false, align: "left",
          font: { size: 11, color: "#888" },
          text: strokes.length + " strokes · " + cov + " · " + (pieces.length - 1) + " saved intervals · computed " +
                new Date().toISOString().slice(11, 19) + " UTC" + (notes.length ? "<br>" + safe(notes.join(" · ")) : "") }]
      }
    };
  }
}
