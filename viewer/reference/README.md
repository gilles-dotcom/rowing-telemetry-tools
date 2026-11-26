# Reference components (not runnable here)

Chart components from the original web app: stroke curves with hover, boat pace and acceleration, session
timelines, GPS map, metric tables, a piece comparison panel (`SessionPieceComparePanel.tsx`), the parsers for the curve data, and `video-alignment.ts` (the video-to-session time maths, see `docs/video-sync.md`). They depend on the app's data
loading, which is not part of this repository. Kept so the chart ideas can be reused.

Lesson from the comparison panel: a comparison should offer the same channels and the same show/hide per rower
as the normal session view, and keep port and starboard apart for sculling. Ours started with a fixed, older
chart set and a single "focus seat", and coaches could not compare what they had just looked at.
