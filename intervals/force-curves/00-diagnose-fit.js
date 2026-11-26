// Temporary replacement for the PeachCurves field script, only if the optimized importer fails.
// Keep Processes fit file messages enabled. This does not read curve arrays.
{
  const fit = icu.fit;
  if (!fit) throw new Error("FIT data unavailable in this context");
  let records = 0;
  for (let i = 0; i < fit.length; i++) if (fit[i]._num === 20) records++;
  "FIT accessible: " + records + " record messages";
}
