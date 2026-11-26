# Syncing video with telemetry

We got video and Peach telemetry playing in sync, frame by frame, with the force curves and a live dot on the
current stroke. It worked, but it was a lot of work, and almost all of that work was about **clocks**, not
video. This page says what we learned, so you can skip the wrong turns.

The maths is in `viewer/reference/video-alignment.ts` (pure functions, no app code).

## The mapping

One number links a video to a session: the offset between video time and session time.

```
session_ms = video_ms + offset_ms
```

There is also room for a scale (`session_ms = video_ms x (1 + scale_ppm / 1e6) + offset_ms`) for a camera whose
clock runs at a slightly different speed. We wrote a two-anchor solver for it but never used it in the app and
removed it: on our clips one offset per clip was enough. Drift within a clip was not measured.

## Why the automatic offset is not enough

The obvious idea: offset = video start time (UTC) - session time 0 (UTC). Every part of that is unreliable.

- **The logger clock is local time, not UTC**, and its offset is not always the one the date suggests (see
  [time](findings/time.md)). Our app first read it as UTC, so every automatic offset was off by 1 or 2 hours.
  After that was fixed, offsets were still often **several seconds** off: the logger clock itself is not exact.
- **The video start time depends on where it comes from.** In order of trust:
  1. a time stamp in the file name, when the camera writes one;
  2. the container's creation date with time zone (iPhone: `com.apple.quicktime.creationdate`), the best one;
  3. the MP4/MOV `mvhd` creation time: no time zone, so you have to guess it;
  4. the file's modified date: rewritten by copying, AirDrop or export. Treat it as a hint only.
- **The camera clock** is a third clock. A phone synced to the network is close; an action camera set by
  hand can be minutes off.
- **The time zone of the session** must come from the recording side (the camera's embedded time zone), never
  from the computer of the person who uploads the files.

So you have three clocks (logger, camera, the time zone you assume), and one wrong guess shifts everything.
An offset that is 1 s off shows the right curve on the wrong stroke, and it looks perfectly plausible.

## What worked: anchor on a frame

1. Start from the automatic offset if the metadata is good, as a rough guess.
2. Step the video **frame by frame** to an event you can see: a catch (blade enters) or a finish.
3. Pick the matching stroke in the telemetry (previous / current / next catch, or its finish).
4. Save `offset_ms = event_session_ms - video_ms`. That offset is now **manual** and must never be replaced by
   a new automatic one, for example after someone edits the session start time.

Store which kind the offset is (`auto`, recomputed on every load, or `manual`, kept as anchored). When in
doubt, keep the manual one: a coach's anchor is worth more than a recomputed guess.

## Wrong turns

1. **Snapping to the nearest stroke automatically.** A video can start mid-stroke, so the nearest catch is
   often the wrong stroke, shown with full confidence. Do not snap stroke identity silently.
2. **Mixing stroke identity and display phase.** We added a small visual phase (to make the live dot meet the
   blade) and applied it before choosing the stroke. Then the dot sat in one stroke and the curve in another,
   or the page asked for a stroke that was not loaded and showed an empty chart. Keep them apart: the offset
   picks the stroke; the phase (bounded, here +-3 s) moves only the dot.
3. **Switching the curve at the catch.** For a coach the drive matters most. Keep the current stroke's curve on
   screen until the finish, then switch during the recovery. Stored curves still run catch to catch.
4. **Too many alignment controls on the main screen.** Offset, phase and lock controls confused coaches. Keep
   frame stepping and looping visible, put alignment in a separate setup step.
5. **Timer-driven playback.** Use the browser's `requestVideoFrameCallback` so the telemetry follows the frame
   that is actually shown; publish directly on seek, pause and frame step.

## How to measure your clocks

For each video you align by hand, write down: the automatic offset, the anchored offset, their difference
(the skew), and where the video time came from. Then:

- the same skew for one logger across cameras means the logger clock is off: correct it per session;
- a skew that changes per camera or session means the clocks are independent: anchor every video;
- only trust rows where the video time came from a container date with time zone.

We never collected enough rows to put numbers here. GPS gives a UTC time inside the file (PowerLine's Excel
lists it, see [time](findings/time.md)), which would make the logger side exact; we did not build that.

## Practical tips

- Video stays on your own computer. Browsers can play local files frame-accurately (Chrome and Edge can keep
  access to a folder); hosted players such as YouTube cannot step frames reliably.
- Film one clear event at the start of each clip (a catch seen side-on), so anchoring takes seconds.
- Write the anchored offset down per clip; it is the only thing you need to keep.
