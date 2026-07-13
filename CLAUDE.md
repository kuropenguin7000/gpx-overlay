# GPX Telemetry Overlay Project

The user makes vertical (9:16) cycling POV videos in CapCut desktop and composites
animated telemetry overlays generated from Strava GPX exports. Designs live in
**external SVG files** and are driven by one Python renderer — author a design once
(e.g. in Claude design, saved as a plain SVG) and feed it in; no Python per design.

## The tool: `render_overlay_svg.py`

Self-contained: parses the GPX, computes all channels, substitutes `{{ expr | fmt }}`
placeholders in an SVG template per frame, rasterizes to transparent RGBA with
**resvg-py**, and pipes frames to ffmpeg (qtrle `.mov`).

```
%LOCALAPPDATA%\Programs\Python\Python312\python.exe render_overlay_svg.py ^
  --gpx ride.gpx --start 05:53:00 --end 05:54:00 --design designs\retro_analog.svg
```

- `--design PATH` — the SVG template (default `designs\retro_analog.svg`).
- `--start` / `--end` — local wall-clock `HH:MM:SS` (matches the on-screen clock; the
  user gives ranges this way) or plain ride-seconds.
- `--png check.png --png-at 05:53:30` — single-frame preview (instant).
- `--jobs N` — parallel workers (default 0 = all CPUs; 1 = single). Frames are
  independent; ~40 fps on 20 cores, so a 3-min clip renders in ~2 min. `feDropShadow`
  filters cost more (~20 fps).
- `--alpha green|prores|vp9` — fallbacks (default qtrle). `--tz` (default +7 WIB),
  `--weight` / `--age` (calorie formula, though calories aren't a default channel).
- Output: transparent qtrle `.mov`, auto-named `<gpx>_<design>_<HHMMSS-HHMMSS>.mov`
  next to the GPX, 1080x1920 @ 30 fps.
- Deps: pillow, numpy, **resvg-py** (`pip install resvg-py`; native Windows wheel,
  uses installed system fonts — Consolas, Segoe UI, Bahnschrift all work). Python 3.12
  user-scope. ffmpeg auto-detected (FFMPEG_PATH / PATH / winget Gyan.FFmpeg).

## How designs work

- **The full template contract is in `DESIGNS.md`** — channel table, placeholder
  syntax, helper functions, gotchas. Read it before authoring or editing a design.
- Channels include speed / hr / ele / dist_km / grade / incl / acc / climb, clock
  (clock12 / clock24 / ampm / …), duration, and clip timeline (frame / clip_t) for
  in-SVG intros, caption fades, end cards.
- `expr` is a restricted Python expression over channel names + helpers (clamp / lerp /
  pick / zone / zone_color / …). SVG comments are stripped before substitution, so
  doc examples with `{{ }}` in comments are safe.
- Authoring space is the SVG `viewBox` (designs use `0 0 405 720`); the renderer scales
  it to 1080x1920. Don't set width/height on `<svg>` or paint a full-canvas background.

## Social-media safe zones — REQUIRED for every design

Overlays are posted to TikTok / Reels / Shorts, whose UI chrome sits on top of the
video. **Every new design must keep all its elements inside the safe box so nothing is
hidden by platform buttons or captions.** In the `405x720` design space (= fractions of
1080x1920):

- **Right ~15% (x > 345)** — action buttons (avatar, like, comment, share, audio).
  Keep content at `x <= 345`. This is why designs are center- or left-anchored.
- **Bottom ~18% (y > 585)** — caption/description, username, music, progress bar.
  Keep content at `y <= 585`; the bottom-left corner is the worst (caption text).
- **Top ~8% (y < 56)** — For You/Following tabs (top-center) and search (top-right).
  Keep content at `y >= 56`.
- **Left**: small margin, `x >= 16`.

So the safe rectangle is roughly **x ∈ [16, 345], y ∈ [56, 585]**; the bottom-right
corner is doubly unsafe (buttons + caption). Prefer lifted, center/left layouts. When
creating a new design, verify a PNG preview keeps everything in this box before
rendering video.

## Existing designs (`designs\`)

- `retro_analog.svg` — warm skeuomorphic analog dial. Paper-cream (#F3EAD8) / dark ink
  (#2E2418) / red (#C2452D), **Consolas** monospace, hard offset "sticker" shadows.
  Time pill, DIST/GRADE pill, analog speed dial (13 ticks every 28° from 220°, red
  needle 0-60 km/h), HEART/ELEV cards (heart is a drawn path, Consolas lacks ♥), warm
  vignette. Component fills ~90% opaque. User-approved as pixel-matching the original.
- `minimal.svg` — clean centered HUD. **Segoe UI** (weight-300 hero number), thin speed
  ring 0-40 km/h with a mint (#57D9C6) arc + rounded caps, hairline DIST/HR/ELEV row,
  soft `feDropShadow` shadows. Lower cluster lifted (`translate(0 -28)`) to clear the
  caption bar.

Copy either design as a starting point for a new one.

## Authoring gotchas (already hit)

- Static SVG can't auto-size a box to its text → size pills/cards for **worst-case ride
  values** (monospace helps); e.g. the DIST/GRADE pill fits `18.1km  +15%`.
- `xml:space="preserve"` also preserves the source indentation, so keep a multi-`tspan`
  `<text>` on ONE line or the leading whitespace shifts the text right.
- Claude-design HTML exports (like `Telemetry_Overlays.html`) are compiled React bundles
  that only render in a browser — NOT usable here. The user should provide a plain
  self-contained SVG (9:16, transparent) with the placeholders.

## CapCut format facts (user-tested 2026-07)

- qtrle `.mov` imports **with transparency** — the working format. No chroma key.
- VP9 webm alpha: CapCut ignores alpha, shows black background. Don't use.
- ProRes 4444 works in principle but is far too large (~57 GB/ride).
- Chroma-green keying degraded thin text and edges — that's why alpha is default.

## Workflow preferences

- The user's footage clips are short (~3 min max): render only requested ranges,
  never the full ride unprompted.
- For a new/changed design, show a PNG frame (or ~1-min preview clip) first; the user
  verifies in CapCut before longer renders.
- Rides are in Yogyakarta (WIB, UTC+7); GPX timestamps are UTC at 1 Hz with lat/lon,
  elevation, time, and heart rate — speed/distance/acceleration/climbed are derived.

## Files in this folder

- git repo: github.com/kuropenguin7000/gpx-overlay (pushed unsigned as rafi.rahman).
  `render_overlay_svg.py` (the renderer), `designs\` (`retro_analog.svg`, `minimal.svg`),
  `DESIGNS.md` (template contract), `README.md`, `.gitignore`.
- The three old Pillow renderers (`render_overlay.py`, `_centered.py`,
  `_retro_analog.py`) were retired 2026-07-13 in favor of the SVG pipeline; the
  retro-analog design lives on as `designs\retro_analog.svg`. They remain in git history.
- `ride.gpx` — the user's working GPX (gitignored). Ride of 2026-07-07 05:39:37 WIB,
  1:10:33, 18.06 km.
- `Telemetry_Overlays.html` — reference mockups from Claude design (compiled React
  bundle; huge embedded base64). Not directly usable by the pipeline.
- `Captures\`, `sepeda\` — the user's own folders; leave alone.
