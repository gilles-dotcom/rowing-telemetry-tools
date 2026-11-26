// PeachCurves shard importer (v3). Activity field, type Text, "Processes fit file messages" ON.
// Create one field per shard: codes PeachCurves1, PeachCurves2, ... PeachCurves<SHARDS>.
// Paste this same script into each and change ONLY the SHARD number below.
// Each shard reads every SHARDS-th FIT message, so every shard alone already
// covers the whole session evenly; together they cover every stroke exactly once.
{
  const SHARD = 1;    // 1..SHARDS  <- change per field
  const SHARDS = 4;   // same value in every shard field
  const N = 21;       // curve points kept per stroke
  const A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  const fit = icu.fit;
  if (!fit || !fit.length) throw new Error("FIT data unavailable: enable Processes fit file messages");
  const start = Date.parse(icu.activity.start_date) / 1000;
  if (!Number.isFinite(start)) throw new Error("Missing activity start_date");
  const names = ["ForceCurvePort", "ForceCurveStarboard"];
  const out = [];
  for (let i = SHARD - 1; i < fit.length; i += SHARDS) {
    const m = fit[i];
    if (m._num !== 20) continue;
    const nf = m.InstrokePointCount, n = nf ? nf.value : 0;
    if (!(n >= 2 && n <= 127)) continue;
    const tf = m.timestamp;
    let t = tf ? Number(tf.value) : NaN;
    if (!Number.isFinite(t)) continue;
    if (t > 1e11) t /= 1000;                 // Unix ms
    t = t < 1.5e9 ? t + 631065600 - start   // FIT epoch seconds
                  : t - start;              // Unix seconds
    let s = "", any = false;
    for (let k = 0; k < 2; k++) {
      const f = m[names[k]], v = f ? f.value : null;
      for (let j = 0; j < N; j++) {
        let c = 4095;
        if (v && v.length >= n) {
          const p = j * (n - 1) / (N - 1), a = Math.floor(p), b = a + 1 < n ? a + 1 : a;
          const va = v[a], vb = v[b];
          if (va != null && vb != null && va < 6553 && vb < 6553) {
            c = Math.round((va + (vb - va) * (p - a)) * 2);
            c = c < 0 ? 0 : c > 4094 ? 4094 : c;
            any = true;
          }
        }
        s += A[c >> 6] + A[c & 63];
      }
    }
    if (any) out.push(Math.round(t * 10) / 10 + ":" + s);
  }
  "PC3|" + SHARD + "/" + SHARDS + "|" + N + "|" + out.length + "|" + out.join(";");
}
