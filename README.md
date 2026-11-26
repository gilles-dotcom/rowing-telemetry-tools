# rowing-telemetry-tools

Findings and tools for working with **your own Peach rowing data**: read `.peach-data` files, turn them
into FIT files for training platforms, add heart rate, look at sessions side by side, and check the result
against PowerLine's own Excel export.

This is a one-time publication of two years of work. It is shared as is, it is **not maintained**,
and there is no support. Forks are welcome. The documentation is the main part: what we found, how we
checked it, and what we got wrong on the way.

## Why I built this

My background is in cycling, where I have experience with the training platforms and how power data can be
used effectively to guide training and to measure performances.

I also coach rowing, and there it was different. The boats have excellent sensors: Peach measures force, angle
and power at every oarlock. But the data mostly stayed in the boathouse, opened one session at a time on a
laptop. It rarely reached the platforms where athletes and coaches plan the rest of their training, and
on-water work did not count in the same picture as ergometer, bike or gym.

These tools started as a way to bring that to rowing: on-water power in your training
platform, correct enough to trust for load and power curves, next to heart rate, and with the details that
only rowing has (seats, sides, force curves) kept. Getting there meant understanding the files, checking every
number against PowerLine, and finding out how stroke data turns into seconds. That work is in `docs/`.

I am moving to a job in another sport, so I am publishing it once, as it is. If it helps raise the level of
how rowing uses its data, it has done its job.

Not affiliated with or endorsed by Peach Innovations, RITA or Garmin. See [NOTICE.md](NOTICE.md).

## What is here

| Folder | What |
|---|---|
| `src/rowtools/` | Read files, stroke rows, FIT export (one record per stroke), heart rate, Intervals.icu upload, compare, Excel checks, overlay page |
| `analysis/` | Stroke segmentation, force and angle curves, technique metrics, wall-clock MMP |
| `docs/` | The findings: file format and time, stroke vs seconds, sweep and sculling, force offsets, video sync, critical power and lactate, wrong turns. Start with [docs/README.md](docs/README.md) |
| `intervals/` | Intervals.icu custom streams and force-curve charts |
| `studies/` | The curves prototype behind the Intervals.icu charts |
| `peachraw-notes/` | First notes on the newer `.peachraw` file, incomplete |
| `bridge/` | A small ANT+ power page encoder from a Garmin bridge experiment |
| `viewer/reference/` | Chart components from the original web app, for reading only (not runnable here) |
| `tests/` | Tests on synthetic files only |

## Start

Tested with Python 3.12 and 3.13.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
rowtools inspect session.peach-data
rowtools convert session.peach-data --utc-offset 120 --out fit/       # one FIT per seat
rowtools convert scull.peach-data --utc-offset 120 --sculling-sport kayaking
rowtools convert session.peach-data --seat 1 --hr watch.fit --hr-offset -6 --utc-offset 120
rowtools view fit/*.fit --out overlay.html                            # open in a browser
rowtools compare fit/*.fit --ftp 250                                  # what a platform will see
rowtools simulate session.peach-data --seat 1                         # stroke vs seconds, same strokes
rowtools verify session.peach-data session.xlsx                       # against PowerLine Excel
INTERVALS_API_KEY=... INTERVALS_ATHLETE_ID=i12345 rowtools push fit/session_seat1.fit
pytest
```

**Sweep and sculling need different activity types.** Platforms keep one power curve, FTP and zone set per
activity type, so two boat families written as "Rowing" get mixed. Use `--sculling-sport kayaking` and read
[docs/sweep-and-sculling.md](docs/sweep-and-sculling.md).

The logger clock in a file is **local time**, not UTC. Give its offset with `--utc-offset` (minutes),
or the tool assumes your computer's time zone and says so. See [docs/findings/time.md](docs/findings/time.md).

## Checked so far

1x, 2x, 2-, 4- and 8+ were checked against PowerLine Excel exports (15 sessions, 51 seats); details and
gaps in [docs/boat-status.md](docs/boat-status.md). **No 4x file was ever tested**, so 4x is off unless you pass
`--experimental`. [docs/untested-boats.md](docs/untested-boats.md) shows how to check one yourself.
Force units are not confirmed, so the FIT export writes no force.

## Built something with this?

I'd love to hear about it. You don't have to, the MIT licence asks nothing more than keeping the notice.
But I'm curious what you made, which boat or setup you used it on, and what you found that I got wrong.
Post it under **Discussions > Show and tell**. I don't maintain this project, so I may be slow to reply and
I can't offer support.

## Licence

MIT, see [LICENSE](LICENSE). Citation: [CITATION.cff](CITATION.cff).
