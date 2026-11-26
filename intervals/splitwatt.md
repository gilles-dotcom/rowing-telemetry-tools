# splitWatt, Intervals.icu custom stream (RP3 power × 1.08)

For **RP3 ergometer** sessions in Intervals.icu: shows the RP3's power multiplied by 1.08 next to the
original, so you can read RP3 sessions on that scale without changing the recorded power. It is not meant for
Peach on-water files: their power comes from the oarlock sensors and is shown as recorded.

## Where
Open any activity → click **Charts** under the timeline chart → **Custom Streams** → **New**

| Field      | Value                                         |
|------------|-----------------------------------------------|
| Name       | splitWatt                                     |
| Code       | SplitWatt (custom stream codes start with a capital) |
| Units      | W                                             |
| Visibility | the activity type your RP3 sessions use       |

## Script
```js
// splitWatt = power × 1.08
{
  let watts = icu.streams.fixed_watts || icu.streams.watts
  for (let i = 0; i < data.length; i++) {
    let w = watts ? watts[i] : null
    data[i] = (w == null) ? null : w * 1.08
  }
}
```

## Notes
- `fixed_watts` is the power stream including any corrections made in Intervals.
  To scale the raw FIT power instead, use `icu.streams.watts`.
- Gaps in power stay gaps (not filled with zeros).
- Check on one RP3 session: splitWatt should read 8% above Power at every point.

## If the stream doesn't show anything
1. Units must be filled in (W). Without units the stream doesn't plot.
2. Recompute existing activities: activity → Actions → Re-analyse.
   The stream isn't calculated for activities that were already uploaded.
3. Turn it on in the chart: Charts (under the timeline) → tick splitWatt.
4. Check that the activity has a Power line. If it has no power, splitWatt stays empty.
