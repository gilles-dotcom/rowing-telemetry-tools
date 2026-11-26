# Wrong turns

Mistakes we made, so you do not have to.

1. **Holding power for 15 s in a 1 Hz export.** It looked smooth and was wrong in both directions
   (NP -11% to +6% per seat). See [stroke or seconds](stroke-or-seconds.md).
2. **Giving each stroke the previous stroke's power.** The device row is written at the end of the stroke.
3. **Treating raw 0 as 0 W**, and later **filling it with the last value** as Excel does. Raw 0 is "no value".
4. **Trusting the sculling total field.** When both sides are missing it repeats the previous stroke.
5. **Reading the header clock as UTC and as the first stroke.** It is local time at session time 0.
6. **Force scale.** Multiplying by 9.81 gave 3,600 W per rower; a factor 2 error gave twice the device power.
   The scale was then tuned to device power, which is why the force unit is still open.
7. **Port and starboard swapped** in sculling: the two power fields are in the opposite order to their
   names in the first code. Totals were right all along, so it went unnoticed.
8. **Distance from the start of recording.** The 50 Hz distance counts from when the logger started (during
   rigging), not from the first stroke.
9. **One NaN in GPS speed** made the whole integrated distance NaN.
10. **Detecting sculling by bit 15 alone.** A 1x looked like two sweep rowers; a stretcher plate looked like
    an extra seat. Use the channel count and the boat type together.
11. **Using seat 1 as the stroke reference** when front seats had no sensors.
12. **Excel as the truth.** It repeats values where the device has none, and its "Average Power" in crew
    boats is not the mean of the seats.
13. **A threshold for "rowing" that was too short**: recovery strokes at 7 to 15 spm disappeared.
14. **Names from the index file**: venue and race labels were read as rower names.
15. **50 Hz work as power.** Force x angular velocity x 0.88 m came out a third above device power. In sculling
    it was worst on starboard: the starboard angle swept much wider than port (about +86 vs +50 degrees at the
    finish), so one 1x read port 114 W vs device 110 W, but starboard 134 W vs device 90 W. Trust the device's
    per-oar power for sculling.
16. **Rounding FIT time stamps with Python's round()**: half-to-even made pairs collide and dropped half
    the stop records. Found by a test while preparing this release.
17. **Sweep and sculling as one activity type.** One power curve, one FTP and one zone set for two boat families.
    See [sweep and sculling](sweep-and-sculling.md).
