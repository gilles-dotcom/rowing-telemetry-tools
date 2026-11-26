// Limit probe. Make 4 temporary activity fields (Text, Processes fit file messages ON):
//   PeachProbe1  TEST = "touch"   reads every FIT message, almost nothing else
//   PeachProbe2  TEST = "all"     reads curves + metrics of EVERY stroke, stores only a summary
//   PeachProbe3  TEST = "quarter" same, every 4th message
//   PeachProbe4  TEST = "size"    stores a 250 KB text, to test the text size limit
// Re-process once. The 07 chart lists what each probe stored (or that it is missing).
// A field that hits "Memory limit exceeded" stays empty. Delete the probes afterwards.
{
  const TEST = "touch";
  const fit = icu.fit;
  if (!fit || !fit.length) throw new Error("FIT data unavailable");
  if (TEST === "size") {
    const KB = 250;
    let s = "PROBE|size|" + KB * 1024 + "|";
    const block = "0123456789abcdef".repeat(64);
    while (s.length < KB * 1024) s += block;
    s.slice(0, KB * 1024);
  } else {
    const step = TEST === "quarter" ? 4 : 1;
    let msgs = 0, strokes = 0, curves = 0, values = 0, checksum = 0;
    for (let i = 0; i < fit.length; i += step) {
      const m = fit[i];
      msgs++;
      if (m._num !== 20 || !m.StrokeDriveTime) continue;
      strokes++;
      if (TEST === "touch") continue;
      for (const name of ["StrokeDriveTime", "CatchPort", "FinishPort", "SlipPort", "WashPort"]) {
        const f = m[name]; if (f) { checksum += Number(f.value) || 0; values++; }
      }
      const nf = m.InstrokePointCount, n = nf ? nf.value : 0;
      for (const name of ["ForceCurvePort", "ForceCurveStarboard"]) {
        const f = n ? m[name] : null, v = f ? f.value : null;
        if (v && v.length >= n) { curves++; for (let j = 0; j < n; j += 4) checksum += v[j] || 0; }
      }
    }
    "PROBE|" + TEST + "|ok|messages " + msgs + " of " + fit.length + " · strokes " + strokes +
      " · curves " + curves + " · values " + values + " · checksum " + Math.round(checksum);
  }
}
