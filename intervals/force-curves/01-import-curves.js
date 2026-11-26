// Computed activity field: code PeachCurves, type Text.
// Enable Processes fit file messages. Re-process with Keep intervals enabled.
{
  // Intervals' FIT SDK exposes scaled values. Do NOT divide these by 10 again.
  const names = ["ForceCurvePort", "ForceCurveStarboard", "HandleForceCurve"];
  const N = 51;
  const read = (m, name) => m[name] ? m[name].value : null;
  if (!icu.fit) throw new Error("Enable Processes fit file messages and re-process the FIT file.");
  const start = Date.parse(icu.activity.start_date) / 1000 - 631065600;
  if (!Number.isFinite(start)) throw new Error("Missing UTC activity start_date.");
  const time = icu.streams.time;
  const pieces = [{label: "Whole session", lo: -Infinity, hi: Infinity}];
  for (const iv of icu.activity.icu_intervals || []) {
    const lo = time && time[iv.start_index];
    const hi = time && (iv.end_index < time.length ? time[iv.end_index] : time[time.length-1] + 1);
    if (Number.isFinite(lo) && Number.isFinite(hi) && hi > lo)
      pieces.push({label: (iv.label || iv.type || "Piece") + " · " + Math.round(lo) + "-" + Math.round(hi) + " s", lo, hi});
  }
  for (const p of pieces) {
    p.strokes = 0;
    p.sum = names.map(() => Array(N).fill(0));
    p.count = names.map(() => Array(N).fill(0));
  }
  // Iterate the FIT collection directly; do not request a record-message array.
  // Reuse the interpolation buffer rather than allocating arrays per stroke.
  const fit = icu.fit;
  const sample = Array(N).fill(null);
  const matches = Array(pieces.length).fill(false);
  let found = 0;
  for (let mi = 0; mi < fit.length; mi++) {
    const m = fit[mi];
    if (m._num !== 20) continue;
    const n = read(m, "InstrokePointCount");
    const t = read(m, "timestamp");
    if (!Number.isInteger(n) || n < 2 || n > 127 || !Number.isFinite(t)) continue;
    for (let j = 0; j < pieces.length; j++)
      matches[j] = t-start >= pieces[j].lo && t-start < pieces[j].hi;
    let hasCurve = false;
    for (let k = 0; k < names.length; k++) {
      // Read each host array only once and retain only this channel.
      const v = read(m, names[k]);
      if (!v || v.length < n) continue;
      hasCurve = true;
      for (let i = 0; i < N; i++) {
        const pos = i*(n-1)/(N-1), a = Math.floor(pos), b = Math.min(a+1,n-1);
        const va = v[a], vb = v[b];
        sample[i] = Number.isFinite(va) && Number.isFinite(vb) && va !== 65535 && vb !== 65535
          ? va + (vb-va)*(pos-a) : null;
      }
      for (let j = 0; j < pieces.length; j++) {
        if (!matches[j]) continue;
        const sums = pieces[j].sum[k], counts = pieces[j].count[k];
        for (let i = 0; i < N; i++) {
          if (sample[i] !== null) { sums[i] += sample[i]; counts[i]++; }
        }
      }
    }
    if (hasCurve) {
      found++;
      for (let j = 0; j < pieces.length; j++) if (matches[j]) pieces[j].strokes++;
    }
  }
  if (!found) throw new Error("No named Peach curve arrays found. Use the CURVES FIT file; check FIT field names in the script console.");
  JSON.stringify({version:1, points:N, pieces:pieces.map(p => ({
    label:p.label, strokes:p.strokes,
    curves:p.sum.map((v,k)=>v.map((s,i)=>p.count[k][i] ? Math.round(s/p.count[k][i]*10)/10 : null))
  }))});
}
