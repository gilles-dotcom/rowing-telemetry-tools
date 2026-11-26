# Check or decode a boat we have not tested (4x)

No 4x file was tested, because we had none with an Excel export. 4x is off unless you pass
`--experimental`. This is how the other boats were worked out, step by step, with tools in this repository.

You need one session as `.peach-data` and the same session exported from PowerLine to Excel (`.xlsx`),
with the same name. Keep both private: the Excel export contains names, GPS and serial numbers.

Naming trap: the export calls the per-stroke section "Aperiodic 0x800A" and the 50 Hz section "Periodic".

1. **Inspect.** `rowtools inspect FILE --experimental` prints block size and channel count. The count comes
   from the file, never from the boat name. A 4x with the same sensors as our 2x should have 7 + 4 x 6 = 31
   channels (reasoned, unverified). Extra sensors change it.
2. **Match the 50 Hz channels.** `rowtools match-columns FILE.peach-data FILE.xlsx` correlates every raw
   channel with every Excel column and prints r, slope and intercept. On our 1x, 2x, 2-, 4- and 8+ this found
   every channel except 2 and 3 at r = 1.0000 with clean scales (1/256, 1/64, 1/16), without knowing the
   boat. A tested synthetic 4x with shuffled columns is recovered completely.
3. **Accept a match only if** r > 0.999, the runner-up is clearly lower (two seats moving together can reach
   0.998), and a second session gives the same result.
4. **Find the stroke rows.** `rowtools match-strokes FILE.peach-data FILE.xlsx --crew 4` looks for the
   Excel stroke times in the file and then for an offset where `raw - 1001` equals the Excel power. On our
   boats every Excel stroke time was in the file and each seat's power offset was found (agreement 93 to 98%,
   the rest being Excel's repeated values). Note that the best offset can be the "last known" copy that
   Excel shows; prefer the first of two offsets 12 bytes apart.
5. **Write the layout down** (channel order, scales, stroke row length, power offsets) and change
   `samples._build_channel_map` and the stroke row constants in `strokes.py`.
6. **Verify.** `rowtools verify FILE FILE.xlsx --boat 4x --experimental`: how many strokes match, how many
   values are off, how many are "not reported". Explain every difference before trusting the result.
7. **Share** the table from steps 2 and 4 under Discussions, not the session.
