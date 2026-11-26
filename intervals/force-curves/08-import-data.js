// PeachData importer: curves + stroke metrics for EVERY stroke, split over K fields.
// Activity field, type Text, "Processes fit file messages" ON, hidden, Inline off.
// Codes PeachData1 .. PeachData<SHARDS>. Same script in each; change only SHARD.
// Each field reads every SHARDS-th FIT message, so all fields together cover
// every stroke exactly once. Pick SHARDS from the 09-probe results.
{
  const SHARD = 1;    // 1..SHARDS  <- change per field
  const SHARDS = 4;   // same in every PeachData field
  const N = 21;       // curve points per stroke
  // [field, scale, offset]: code = round((value + offset) * scale), 2 chars, 0..4094
  const M = [["StrokeDriveTime", .1, 0], ["StrokeRecoveryTime", .1, 0],
    ["AverageDriveForce", 1, 0], ["PeakDriveForce", 1, 0], ["StrokeWork", 1, 0],
    ["CatchPort", 10, 150], ["CatchStarboard", 10, 150], ["FinishPort", 10, 150], ["FinishStarboard", 10, 150],
    ["SlipPort", 10, 150], ["SlipStarboard", 10, 150], ["WashPort", 10, 150], ["WashStarboard", 10, 150],
    ["EffectiveArcPort", 10, 150], ["EffectiveArcStarboard", 10, 150],
    ["PeakForceAnglePort", 10, 150], ["PeakForceAngleStarboard", 10, 150]];
  const A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  const enc = c => A[c >> 6] + A[c & 63];
  const fit = icu.fit;
  if (!fit || !fit.length) throw new Error("FIT data unavailable: enable Processes fit file messages");
  const start = Date.parse(icu.activity.start_date) / 1000;
  if (!Number.isFinite(start)) throw new Error("Missing activity start_date");
  const out = [];
  for (let i = SHARD - 1; i < fit.length; i += SHARDS) {
    const m = fit[i];
    if (m._num !== 20) continue;
    const df = m.StrokeDriveTime;
    if (!df) continue;                                 // stop-fill record, not a stroke
    const tf = m.timestamp;
    let t = tf ? Number(tf.value) : NaN;
    if (!Number.isFinite(t)) continue;
    if (t > 1e11) t /= 1000;
    t = t < 1.5e9 ? t + 631065600 - start : t - start;
    // metrics
    let ms = "";
    for (let k = 0; k < M.length; k++) {
      const f = m[M[k][0]], v = f ? Number(f.value) : NaN;
      let c = 4095;
      if (Number.isFinite(v)) { c = Math.round((v + M[k][2]) * M[k][1]); c = c < 0 ? 0 : c > 4094 ? 4094 : c; }
      ms += enc(c);
    }
    // curves (port, starboard), resampled to N points
    let cs = "";
    const nf = m.InstrokePointCount, n = nf ? nf.value : 0;
    for (const name of ["ForceCurvePort", "ForceCurveStarboard"]) {
      const f = n >= 2 && n <= 127 ? m[name] : null, v = f ? f.value : null;
      for (let j = 0; j < N; j++) {
        let c = 4095;
        if (v && v.length >= n) {
          const p = j * (n - 1) / (N - 1), a = Math.floor(p), b = a + 1 < n ? a + 1 : a;
          const va = v[a], vb = v[b];
          if (va != null && vb != null && va < 6553 && vb < 6553) {
            c = Math.round((va + (vb - va) * (p - a)) * 2); c = c < 0 ? 0 : c > 4094 ? 4094 : c;
          }
        }
        cs += enc(c);
      }
    }
    out.push(Math.round(t * 10) / 10 + ":" + ms + cs);
  }
  "PD1|" + SHARD + "/" + SHARDS + "|" + N + "|" + out.length + "|" + out.join(";");
}
