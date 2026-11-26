# Time

**The header clock is the logger's local time at session time 0** (measured on 15 files).

- A `u32` at byte 24 of the file is seconds since 1970 on the logger's clock.
- Session time 0 is the time base of everything else in the file (stroke rows and 50 Hz blocks count
  milliseconds from it). It is **not** the time of the first stroke, which came 6 to 657 s later in our files.
- PowerLine's Excel export lists one GPS fix with its UTC time and session time. GPS UTC at time 0 was
  exactly 1 h or 2 h before the header clock in all 15 files, within 1 s. So the clock is local time.
- Within one summer some files were 1 h off and others 2 h. A logger clock does not necessarily follow
  summer time. Do not assume the offset from the date.

What this means:

- `rowtools convert` needs the logger's UTC offset (`--utc-offset`, minutes) or a start time in UTC. If you
  give neither, it uses your computer's time zone and says so.
- Our old app put the first stroke at the header time, and read the header as UTC. Both shift a FIT file in
  time, which is why heart-rate merges needed large manual offsets.
- FIT stores whole seconds. Stroke records are rounded to the nearest second; two strokes inside one
  second keep their order by moving the second one by a second.
