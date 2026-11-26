// Experimental Plotly chart: inspect selection context without FIT parsing.
// The candidate names below are hypotheses, NOT documented Intervals APIs.
// Use as a separate chart. Do not enable FIT processing or alter PeachCurves.
{
  const labels = [], values = [];
  function add(label, value) { labels.push(label); values.push(String(value)); }
  function get(obj, key) { try { return obj == null ? null : obj[key]; } catch (e) { return null; } }
  function show(label, obj) {
    if (obj == null) { add(label, "not exposed"); return; }
    let found = false;
    for (const key of ["start_time", "end_time", "start_index", "end_index", "start", "end", "from", "to"]) {
      const v = get(obj, key);
      if (typeof v === "number" && Number.isFinite(v)) { add(label + "." + key, v); found = true; }
    }
    if (!found) add(label, "present; no recognised numeric boundaries");
  }
  add("Script evaluated at (UTC)", new Date().toISOString());
  show("global selection", typeof selection === "undefined" ? null : selection);
  show("global interval", typeof interval === "undefined" ? null : interval);
  show("global range", typeof range === "undefined" ? null : range);
  show("icu.selection [experimental]", get(icu, "selection"));
  show("icu.interval [experimental]", get(icu, "interval"));
  show("icu.range [experimental]", get(icu, "range"));
  // Documented streams: see whether their extent changes after a timeline drag.
  // Read only the first/last values, never copy or serialize the stream.
  const time = icu.streams.time;
  if (time && time.length) {
    add("Time stream samples", time.length);
    add("First elapsed second", time[0]);
    add("Last elapsed second", time[time.length - 1]);
  }
  chart = {data: [{type: "table", columnwidth: [55, 45],
    header: {values: ["Selection probe", "Observed value"], align: "left"},
    cells: {values: [labels, values], align: "left", height: 26}}],
    layout: {title: {text: "Drag the POWER timeline above, then compare this table"},
      height: Math.max(440, labels.length * 28 + 100), margin: {l: 10, r: 10, t: 65, b: 10}}};
}
