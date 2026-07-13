# gpx-overlay

Turn a GPX ride recording into an animated **telemetry overlay** for vertical (9:16)
cycling videos. Output is a **transparent `.mov`** you drop straight on top of your
footage in CapCut — no chroma key, no keying artifacts.

The design lives in an **SVG file**, not in code. Author a design once (in Claude
design, or by hand) with `{{ channel }}` placeholders, and reuse it for any clip.

## Quick start

```
pip install pillow numpy resvg-py          # one-time (plus ffmpeg on PATH)

python render_overlay_svg.py --gpx ride.gpx --start 05:53:00 --end 05:54:00 \
  --design designs/retro_analog.svg
```

`--start` / `--end` take **wall-clock time** `HH:MM:SS` (matching the overlay's clock)
or ride-seconds. Output is auto-named next to the GPX, 1080×1920 @ 30 fps, transparent.

Preview a single frame first (instant):

```
python render_overlay_svg.py --gpx ride.gpx --design designs/minimal.svg \
  --png check.png --png-at 05:53:30
```

## Designs (`designs/`)

| File | Look |
|---|---|
| `retro_analog.svg` | warm analog speed dial, cream/ink/red, monospace, sticker shadows |
| `minimal.svg` | clean centered HUD: thin speed ring + light hero number, hairline stats |

## Options

| Flag | Meaning |
|---|---|
| `--design PATH` | which SVG to render (default `designs/retro_analog.svg`) |
| `--png f.png --png-at 05:53:30` | render one preview frame |
| `--jobs N` | parallel workers (default: all CPU cores) |
| `--out clip.mov` | custom output path |
| `--alpha green` | chroma-green MP4 instead of transparent (also `prores`, `vp9`) |
| `--tz 7` | UTC offset of the ride (default +7 WIB) |

## Make your own design

Author a self-contained SVG (9:16, transparent) and put placeholders like
`{{ speed | .0f }}`, `{{ clock24 }}`, `{{ zone_color(hr) }}`, or
`rotate({{ ... }})` on the parts that change per frame. Copy `retro_analog.svg` or
`minimal.svg` as a starting point.

**See [DESIGNS.md](DESIGNS.md)** for the full list of channels, the placeholder
syntax, helper functions, and the social-media safe zones every design should respect.

## Using in CapCut

Import the `.mov` and place it on the track **above** your footage — the background is
already transparent. Don't apply chroma key / background removal.

> Why qtrle? In CapCut desktop it imports with working transparency. VP9 webm alpha
> shows a black background, ProRes 4444 files are impractically large, and green-screen
> keying erodes thin text and edges.

## GPX requirements

GPX 1.1 with ~1 Hz trackpoints containing `lat`/`lon`, `<ele>`, `<time>`, and heart
rate in the Garmin TrackPointExtension (`gpxtpx:hr`) — what Strava exports. Speed,
distance, grade, acceleration, and climb are derived. Missing HR or elevation default
to 0.
