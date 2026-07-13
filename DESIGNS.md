# Dynamic SVG designs (`render_overlay_svg.py`)

`render_overlay_svg.py` keeps the **design in an external `.svg` file** instead of
hard-coding it in Python. It parses the GPX, computes all channels, and pipes qtrle to
ffmpeg; only the drawing lives in the SVG. Author a design once, reuse it for any clip
— no Python changes.

## Setup (one time)

```
%LOCALAPPDATA%\Programs\Python\Python312\python.exe -m pip install resvg-py
```

`resvg-py` is the SVG rasterizer (native Windows wheel, no extra system libs). It
uses installed system fonts, so Consolas / Bahnschrift work out of the box.

## Run

Standard CLI, plus `--design` and `--jobs`:

```
python render_overlay_svg.py --gpx ride.gpx --start 05:53:00 --end 05:54:00 ^
    --design designs\retro_analog.svg
```

- `--design PATH` — the SVG template (default `designs\retro_analog.svg`).
- `--start` / `--end` — clock `HH:MM:SS` (matches the on-screen clock) or ride-seconds.
- `--png check.png --png-at 05:53:30` — single-frame preview (instant; verify before a full render).
- `--jobs N` — parallel render workers (default `0` = all CPUs; `1` = single process).
  Video frames are independent, so this scales near-linearly. On a 20-core machine a
  1-min clip renders in well under a minute; a 3-min clip in ~2 min.
- `--alpha qtrle|prores|vp9|green` — default `qtrle` (transparent `.mov` for CapCut).
- `--tz` (UTC offset, default +7 WIB), `--weight` / `--age` (calorie formula inputs).
- Output auto-named `<gpx>_<design>_<HHMMSS-HHMMSS>.mov` next to the GPX.

## Authoring contract

A design is a normal SVG with `{{ ... }}` placeholders on the parts that change per
frame. Everything else is static SVG (drawn once by the rasterizer).

1. **Canvas** — put the design in a `viewBox` of your choosing; the renderer scales
   it to 1080x1920 (9:16). Use a 9:16 `viewBox` so nothing stretches (retro_analog
   uses `0 0 405 720`). Do **not** set `width`/`height` on `<svg>` — the driver
   supplies them.
2. **Transparency** — don't paint a full-canvas background rect (unless you want one).
   Empty areas stay transparent, which is what CapCut composites over your footage.
3. **Fonts** — reference installed system fonts by name (`font-family="Consolas"`,
   `"Bahnschrift"`). `font-weight="bold"` selects the bold face.
4. **Placeholders** — `{{ expr }}` or `{{ expr | fmt }}`:
   - `expr` is a Python expression over the channels + helpers below.
   - `fmt` is an optional Python format spec: `.0f`, `.1f`, `+.0f`, `02d`, …
   - Examples:
     ```
     <text ...>{{ speed | .0f }}</text>
     <text ...>{{ clock12 }}</text>
     transform="rotate({{ -125 + 250*clamp(speed/60,0,1) | .2f }} 88 494)"
     stroke="{{ zone_color(hr) }}"
     stroke-dasharray="{{ clamp(speed/40,0,1)*408 | .1f }} 408"
     ```

### Channels (per frame)

| name | meaning | unit |
|------|---------|------|
| `speed` | speed | km/h |
| `hr` | heart rate | bpm |
| `ele` | elevation | m |
| `dist_m` / `dist_km` | distance | m / km |
| `grade` | road grade | % |
| `incl` | inclination angle | deg |
| `acc` | acceleration | g |
| `climb` | metres climbed since clip start | m |
| `climb_total` | metres climbed since ride start | m |
| `duration` | elapsed ride time | s (int) |
| `dur_h`, `dur_m`, `dur_s` | elapsed h / m / s parts | int |
| `duration_hms` | `"H:MM:SS"` elapsed | string |
| `clock_s` | wall-clock seconds since local midnight | int |
| `hour24`, `hour12`, `minute`, `second` | wall-clock parts | int |
| `ampm` | `"AM"` / `"PM"` | string |
| `clock12` | `"H:MM:SS AM"` | string |
| `clock24` | `"HH:MM:SS"` | string |
| `frame`, `nframes`, `fps` | clip frame index / count / 30 | int |
| `clip_t`, `clip_dur` | seconds since clip start / clip length | float |

`frame` / `clip_t` let a design animate intros, caption fades, or end cards itself
(e.g. `opacity="{{ clamp(clip_t/0.4,0,1) | .2f }}"`).

### Helper functions (usable in `expr`)

`clamp(x, lo=0, hi=1)`, `lerp(a, b, t)`, `pick(i, v0, v1, …)` (categorical select),
`zone(hr)`→1..5, `zone_color(hr)`→hex, `zone_name(hr)`→`"Z3"`, plus `min max abs
round int float sin cos tan radians hypot sqrt pi` and the constant `HR_MAX` (190).

Zones use 60/70/80/90 % of `HR_MAX`; colours Z1 grey → Z5 red.

## Social-media safe zones (keep every design inside these)

Overlays get posted to TikTok / Reels / Shorts, which draw their own buttons and text
on top of the video. Keep **all** design elements inside the safe box so nothing is
covered. In the `405x720` design space (= fractions of 1080x1920):

| Zone | Avoid | Why |
|------|-------|-----|
| Right column | `x > 345` (right ~15%) | avatar, like, comment, share, audio buttons |
| Bottom band | `y > 585` (bottom ~18%) | caption/description, username, music, progress |
| Top band | `y < 56` (top ~8%) | For You/Following tabs, search icon |
| Left edge | `x < 16` | small margin |

**Safe rectangle: `x ∈ [16, 345]`, `y ∈ [56, 585]`.** The bottom-right corner is the
worst (buttons *and* caption overlap it). Prefer lifted, center- or left-anchored
layouts — that's why `retro_analog.svg` hugs the left and `minimal.svg` is centered
with its lower cluster lifted. Check a PNG preview against this box before rendering
video.

## Gotchas (learned building `retro_analog.svg`)

- **No text auto-fit.** Static SVG can't size a box to fit its text automatically.
  Size pills/cards for the *worst-case* value over a ride (e.g. `18.1km`, `+15%`);
  monospace fonts make this predictable. Or right-pad numeric fields to a fixed width.
- **`xml:space="preserve"` preserves source indentation.** If you need literal double
  spaces inside a `<text>`, keep the whole element (and its `<tspan>`s) on **one line**
  — otherwise the newline + indent render as leading blanks and shift the text.
- **Comments are stripped** before substitution, so you can safely put `{{ ... }}`
  examples in `<!-- -->` comments.
- **resvg feature support** is broad (gradients, most filters, transforms,
  `letter-spacing`, `dominant-baseline`) but not a full browser. Preview a PNG frame
  before committing to a long render.

## Getting a design from Claude

Ask Claude design for a **single self-contained SVG**, 9:16, transparent background,
using the channel placeholders above — rather than a React/HTML artifact (those are
compiled bundles that only render in a browser and can't be rasterized directly).
`designs\retro_analog.svg` and `designs\minimal.svg` are full worked examples — copy
either as a starting point.
