// PeachStrokes1: per-stroke metrics for the table in 07-curves-table-chart.js.
// Activity field, type Text, "Processes fit file messages" ON, hidden, Inline off.
// Reads only numbers (no curve arrays) from every stroke record.
// Power, rate, split, HR and distance come from Intervals' own interval values.
// If this hits "Memory limit exceeded": set SHARDS = 2, create PeachStrokes2 with
// SHARD = 2, and re-process. The chart merges PeachStrokes1..8 automatically.
{
  const SHARD = 1, SHARDS = 1;
  const COLS = ["StrokeDriveTime", "StrokeRecoveryTime",
    "AverageDriveForce", "PeakDriveForce", "StrokeWork",
    "CatchPort", "CatchStarboard", "FinishPort", "FinishStarboard",
    "SlipPort", "SlipStarboard", "WashPort", "WashStarboard",
    "EffectiveArcPort", "EffectiveArcStarboard", "PeakForceAnglePort", "PeakForceAngleStarboard"];
  const fit = icu.fit;
  if (!fit || !fit.length) throw new Error("FIT data unavailable: enable Processes fit file messages");
  const start = Date.parse(icu.activity.start_date) / 1000;
  if (!Number.isFinite(start)) throw new Error("Missing activity start_date");
  const rows = [];
  for (let i = SHARD - 1; i < fit.length; i += SHARDS) {
    const m = fit[i];
    if (m._num !== 20 || !m.StrokeDriveTime) continue;   // skip stop-fill records
    const tf = m.timestamp;
    let t = tf ? Number(tf.value) : NaN;
    if (!Number.isFinite(t)) continue;
    if (t > 1e11) t /= 1000;
    t = t < 1.5e9 ? t + 631065600 - start : t - start;
    let r = String(Math.round(t * 10) / 10);
    for (let k = 0; k < COLS.length; k++) {
      const f = m[COLS[k]], v = f ? Number(f.value) : NaN;
      r += "," + (Number.isFinite(v) ? Math.round(v * 10) / 10 : "");
    }
    rows.push(r);
  }
  if (!rows.length) throw new Error("No stroke records with StrokeDriveTime found");
  "PS1|" + SHARD + "/" + SHARDS + "|t," + COLS.join(",") + "|" + rows.length + "|" + rows.join(";");
}
