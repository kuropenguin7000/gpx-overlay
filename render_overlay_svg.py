"""SVG-template telemetry overlay renderer - vertical 9:16, transparent qtrle .mov.

Same GPX engine as render_overlay.py (parsing, channel math, ffmpeg piping), but
the *design lives in an external SVG file* (--design) instead of being coded in
Python. Per frame the driver builds a dict of telemetry channels, substitutes
`{{ expr | fmt }}` placeholders in the SVG, and rasterizes to transparent RGBA
with resvg, piping frames to ffmpeg.

Author a design once as an .svg (e.g. in Claude design, exported/saved as plain
SVG) with placeholders bound to the channels below, then reuse it for any clip -
no Python changes. See DESIGNS.md for the full template contract.

Usage:
  python render_overlay_svg.py --gpx ride.gpx --start 05:53:00 --end 05:54:00
  python render_overlay_svg.py --gpx ride.gpx --design designs/retro_analog.svg \
      --png check.png --png-at 05:53:30

--start/--end accept local clock HH:MM:SS (matches the on-screen clock) or plain
ride-seconds. Default output: transparent QuickTime Animation .mov (qtrle),
confirmed working in CapCut desktop.
"""
import argparse, glob, io, math, multiprocessing as mp, os, re, shutil, subprocess, sys, time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import numpy as np
from PIL import Image
import resvg_py


def find_ffmpeg():
    """FFMPEG_PATH env var, then PATH, then the winget Gyan.FFmpeg install."""
    p = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
    if p:
        return p
    hits = glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\bin\ffmpeg.exe"),
        recursive=True)
    if hits:
        return hits[0]
    sys.exit("ffmpeg not found: install it (winget install Gyan.FFmpeg) "
             "or set FFMPEG_PATH")


W, H, FPS = 1080, 1920, 30
GREEN = (0, 255, 0)
HR_MAX = 190.0                        # heart-rate ceiling for zone helpers

# ---------------------------------------------------------------- gpx -> telemetry
NS = {"g": "http://www.topografix.com/GPX/1/1",
      "tpx": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"}


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def load_gpx(path, tz_offset_h, weight_kg, age_y):
    """Parse GPX, resample to 1 Hz, derive all display channels. Sets globals."""
    global lat, lon, ele, hr, spd, dist, T, secs, incl, climb, cal, acc
    global START_SEC_LOCAL, START_DATE_LOCAL

    root = ET.parse(path).getroot()
    pts = root.findall(".//g:trkseg/g:trkpt", NS)
    if not pts:
        sys.exit("no trackpoints found in " + path)
    la, lo, el, h, t = [], [], [], [], []
    for p in pts:
        la.append(float(p.get("lat")))
        lo.append(float(p.get("lon")))
        e = p.find("g:ele", NS)
        el.append(float(e.text) if e is not None else 0.0)
        hh = p.find(".//tpx:hr", NS)
        h.append(float(hh.text) if hh is not None else 0.0)
        t.append(datetime.fromisoformat(p.find("g:time", NS).text.replace("Z", "+00:00")).timestamp())
    la = np.array(la); lo = np.array(lo); el = np.array(el)
    h = np.array(h); t = np.array(t)
    sec = (t - t[0]).astype(int)

    step = np.zeros(len(la))
    for i in range(1, len(la)):
        step[i] = haversine(la[i - 1], lo[i - 1], la[i], lo[i])
    cum = np.cumsum(step)

    T = int(sec[-1]) + 1
    grid = np.arange(T)
    lat = np.interp(grid, sec, la)
    lon = np.interp(grid, sec, lo)
    ele_r = np.interp(grid, sec, el)
    hr = np.interp(grid, sec, h)
    dist = np.interp(grid, sec, cum)
    secs = grid

    # speed km/h from smoothed distance derivative
    kern = np.ones(7) / 7
    ds = np.convolve(dist, kern, mode="same")
    ds[:7] = dist[:7]; ds[-7:] = dist[-7:]
    spd = np.clip(np.gradient(ds) * 3.6, 0, None)
    spd = np.convolve(spd, np.ones(3) / 3, mode="same")

    ele = np.convolve(ele_r, np.ones(9) / 9, mode="same")
    ele[:9] = ele_r[:9]; ele[-9:] = ele_r[-9:]

    grade = np.zeros(T)
    w = 5
    for i in range(T):
        a, b = max(0, i - w), min(T - 1, i + w)
        dd = dist[b] - dist[a]
        if dd > 5:
            grade[i] = (ele[b] - ele[a]) / dd * 100.0
    grade = np.convolve(grade, np.ones(5) / 5, mode="same")
    incl = np.degrees(np.arctan(np.clip(grade, -25, 25) / 100.0))

    climb = np.cumsum(np.clip(np.diff(ele, prepend=ele[0]), 0, None))

    # calories (Keytel, HR-based)
    kcal_min = (-55.0969 + 0.6309 * hr + 0.1988 * weight_kg + 0.2017 * age_y) / 4.184
    cal = np.cumsum(np.clip(kcal_min, 0, None)) / 60.0

    acc = np.gradient(spd / 3.6)
    acc = np.convolve(acc, np.ones(5) / 5, mode="same") / 9.81

    local = datetime.fromtimestamp(t[0], tz=timezone(timedelta(hours=tz_offset_h)))
    START_SEC_LOCAL = local.hour * 3600 + local.minute * 60 + local.second
    START_DATE_LOCAL = local.strftime("%Y/%m/%d")
    dur = f"{T // 3600}:{T % 3600 // 60:02d}:{T % 60:02d}"
    print(f"ride: {START_DATE_LOCAL} {local.strftime('%H:%M:%S')} local, "
          f"duration {dur}, {dist[-1] / 1000:.2f} km")


# ---------------------------------------------------------------- template engine
# Placeholders in the SVG:  {{ expr }}  or  {{ expr | format_spec }}
#   expr  - Python expression over the channel names + helpers below
#   fmt   - a Python format spec, e.g. .0f  .1f  02d   (optional)
# Example:  <text>{{ speed | .0f }}</text>
#           transform="rotate({{ -125 + 250*clamp(speed/60,0,1) | .2f }} 88 470)"
_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)   # stripped so doc examples aren't evaluated

# Training-zone helpers (Z1..Z5 at 60/70/80/90% of HR_MAX)
_ZONE_EDGES = [0.60, 0.70, 0.80, 0.90]
_ZONE_COLORS = ["#9aa0a6", "#3b82f6", "#22c55e", "#f59e0b", "#ef4444"]


def _clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def _lerp(a, b, t):
    return a + (b - a) * t


def _pick(i, *vals):
    """Categorical select: pick(index, v0, v1, ...) clamped to range."""
    i = int(i)
    return vals[max(0, min(i, len(vals) - 1))]


def _zone(hr_bpm):
    frac = hr_bpm / HR_MAX
    z = 1
    for e in _ZONE_EDGES:
        if frac >= e:
            z += 1
    return min(z, 5)


def _zone_color(hr_bpm):
    return _ZONE_COLORS[_zone(hr_bpm) - 1]


def _zone_name(hr_bpm):
    return "Z%d" % _zone(hr_bpm)


# functions/constants exposed to template expressions
SAFE_ENV = {
    "__builtins__": {},
    "clamp": _clamp, "lerp": _lerp, "pick": _pick,
    "zone": _zone, "zone_color": _zone_color, "zone_name": _zone_name,
    "min": min, "max": max, "abs": abs, "round": round,
    "int": int, "float": float,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "radians": math.radians, "hypot": math.hypot, "sqrt": math.sqrt,
    "pi": math.pi, "HR_MAX": HR_MAX,
}


def _fmt_default(val):
    """Format a value with no explicit spec: trim trailing zeros on floats."""
    if isinstance(val, float):
        s = ("%f" % val).rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"
    return str(val)


def render_template(tmpl, ctx):
    """Substitute every {{ expr | fmt }} in tmpl using channel dict ctx."""
    def repl(m):
        expr, sep, fmt = m.group(1).partition("|")
        try:
            val = eval(expr.strip(), dict(SAFE_ENV), ctx)  # noqa: S307 (trusted template)
        except Exception as e:
            sys.exit(f"template error in {{{{{m.group(1).strip()}}}}}: {e}")
        fmt = fmt.strip()
        return format(val, fmt) if fmt else _fmt_default(val)
    return _PLACEHOLDER.sub(repl, tmpl)


def frame_ctx(i, ft, ch, nframes):
    """Build the channel dict for frame i from precomputed per-frame arrays."""
    tsec = int(ft[i])
    clk = int(START_SEC_LOCAL + tsec)
    h24 = clk // 3600 % 24
    h12 = h24 % 12 or 12
    mm = clk % 3600 // 60
    ss = clk % 60
    ampm = "AM" if h24 < 12 else "PM"
    return {
        # live telemetry
        "speed": float(ch["spd"][i]),          # km/h
        "hr": float(ch["hr"][i]),              # bpm
        "ele": float(ch["ele"][i]),            # m
        "dist_m": float(ch["dist"][i]),        # m
        "dist_km": float(ch["dist"][i]) / 1000.0,
        "grade": float(ch["grade"][i]),        # %
        "incl": float(ch["incl"][i]),          # deg
        "acc": float(ch["acc"][i]),            # g
        "climb": float(ch["climb"][i] - ch["climb"][0]),  # m gained this clip
        "climb_total": float(ch["climb"][i]),  # m gained since ride start
        # elapsed ride time
        "duration": tsec,
        "dur_h": tsec // 3600, "dur_m": tsec % 3600 // 60, "dur_s": tsec % 60,
        "duration_hms": "%d:%02d:%02d" % (tsec // 3600, tsec % 3600 // 60, tsec % 60),
        # wall clock
        "clock_s": clk, "hour24": h24, "hour12": h12, "minute": mm, "second": ss,
        "ampm": ampm,
        "clock12": "%d:%02d:%02d %s" % (h12, mm, ss, ampm),
        "clock24": "%02d:%02d:%02d" % (h24, mm, ss),
        # clip timeline (for intros, caption fades, end cards done in-SVG)
        "frame": i, "nframes": nframes, "fps": FPS,
        "clip_t": i / FPS, "clip_dur": nframes / FPS,
    }


def rasterize(svg_str):
    """SVG string -> RGBA PIL image at W x H (transparent background)."""
    data = bytes(resvg_py.svg_to_bytes(svg_string=svg_str, width=W, height=H))
    img = Image.open(io.BytesIO(data))
    return img if img.mode == "RGBA" else img.convert("RGBA")


def _render_bytes(tmpl, ctx, alpha):
    """Rasterize one frame to the raw bytes ffmpeg expects (RGBA, or RGB on green)."""
    img = rasterize(render_template(tmpl, ctx))
    if not alpha:                                # flatten onto green for chroma mp4
        img = Image.alpha_composite(Image.new("RGBA", (W, H), GREEN + (255,)), img).convert("RGB")
    return img.tobytes()


# worker globals set once per process (template + alpha flag are constant across frames)
_W_TMPL = None
_W_ALPHA = True


def _worker_init(tmpl, alpha):
    global _W_TMPL, _W_ALPHA
    _W_TMPL, _W_ALPHA = tmpl, alpha


def _worker_render(ctx):
    return _render_bytes(_W_TMPL, ctx, _W_ALPHA)


# ---------------------------------------------------------------- progress bar
_last_bar = [0.0]


def progress_bar(done, total, t_start, width=32):
    now = time.time()
    if done < total and now - _last_bar[0] < 0.1:
        return
    _last_bar[0] = now
    frac = done / total
    filled = int(width * frac)
    bar = "#" * filled + "-" * (width - filled)
    el = now - t_start
    fps = done / el if el > 0 else 0
    eta = (total - done) / fps if fps > 0 else 0
    sys.stdout.write(f"\r[{bar}] {frac*100:5.1f}%  {done}/{total}  "
                     f"{fps:4.1f} fps  ETA {eta:4.0f}s ")
    sys.stdout.flush()


# ---------------------------------------------------------------- main
def parse_when(s):
    """HH:MM[:SS] local clock time (matches on-screen clock) or ride-seconds."""
    if s is None:
        return None
    if ":" in s:
        parts = [int(p) for p in s.split(":")]
        clock = parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
        rel = clock - START_SEC_LOCAL
        if rel < 0:
            rel += 86400
        return float(rel)
    return float(s)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--gpx", required=True, help="path to .gpx file")
    ap.add_argument("--design", default=os.path.join(here, "designs", "retro_analog.svg"),
                    help="SVG template file (default: designs/retro_analog.svg)")
    ap.add_argument("--start", default=None, help="clock HH:MM:SS or ride-seconds")
    ap.add_argument("--end", default=None, help="clock HH:MM:SS or ride-seconds")
    ap.add_argument("--out", default=None, help="output path (.mov)")
    ap.add_argument("--png", default=None, help="render single frame to this PNG")
    ap.add_argument("--png-at", default=None, help="time of the PNG frame")
    ap.add_argument("--alpha", choices=["qtrle", "prores", "vp9", "green"], default="qtrle",
                    help="qtrle = transparent .mov for CapCut (default)")
    ap.add_argument("--tz", type=float, default=7.0, help="UTC offset hours (default WIB +7)")
    ap.add_argument("--weight", type=float, default=70.0, help="rider kg for calories")
    ap.add_argument("--age", type=float, default=30.0, help="rider age for calories")
    ap.add_argument("--jobs", type=int, default=0,
                    help="parallel render workers for video (0 = auto = all CPUs, 1 = single)")
    args = ap.parse_args()

    global ALPHA
    ALPHA = args.alpha != "green"

    if not os.path.isfile(args.design):
        sys.exit(f"design not found: {args.design}")
    with open(args.design, "r", encoding="utf-8") as f:
        tmpl = _COMMENT.sub("", f.read())    # drop comments (may hold doc examples)

    load_gpx(args.gpx, args.tz, args.weight, args.age)

    start_sec = parse_when(args.start) or 0.0
    end_sec = parse_when(args.end)
    if end_sec is None:
        end_sec = T - 1
    start_sec = max(0.0, min(start_sec, T - 2))
    end_sec = max(start_sec + 1, min(end_sec, T - 1))

    f0, f1 = int(start_sec * FPS), int(end_sec * FPS)
    nframes = f1 - f0

    grade = np.tan(np.radians(incl)) * 100.0     # % grade from inclination
    ft = np.arange(f0, f1) / FPS
    ch = {
        "spd": np.interp(ft, secs, spd),
        "hr": np.interp(ft, secs, hr),
        "ele": np.interp(ft, secs, ele),
        "dist": np.interp(ft, secs, dist),
        "grade": np.interp(ft, secs, grade),
        "incl": np.interp(ft, secs, incl),
        "acc": np.interp(ft, secs, acc),
        "climb": np.interp(ft, secs, climb),
    }

    def render_frame(i):
        img = rasterize(render_template(tmpl, frame_ctx(i, ft, ch, nframes)))
        if not ALPHA:                            # flatten onto green for chroma mp4
            bg = Image.new("RGBA", (W, H), GREEN + (255,))
            img = Image.alpha_composite(bg, img).convert("RGB")
        return img

    if args.png:
        at = parse_when(args.png_at)
        if at is None:
            at = start_sec
        i = int((at - start_sec) * FPS)
        render_frame(max(0, min(i, nframes - 1))).save(args.png)
        print("saved", args.png)
        return

    # output path
    out = args.out
    if out is None:
        c0 = int(START_SEC_LOCAL + start_sec)
        c1 = int(START_SEC_LOCAL + end_sec)
        tag = (f"{c0 // 3600 % 24:02d}{c0 % 3600 // 60:02d}{c0 % 60:02d}"
               f"-{c1 // 3600 % 24:02d}{c1 % 3600 // 60:02d}{c1 % 60:02d}")
        ext = {"qtrle": ".mov", "prores": ".mov", "vp9": ".webm", "green": ".mp4"}[args.alpha]
        stem = os.path.splitext(os.path.basename(args.gpx))[0]
        design = os.path.splitext(os.path.basename(args.design))[0]
        out = os.path.join(os.path.dirname(os.path.abspath(args.gpx)),
                           f"{stem}_{design}_{tag}{ext}")

    in_fmt = "rgba" if ALPHA else "rgb24"
    if args.alpha == "prores":
        enc = ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le"]
    elif args.alpha == "qtrle":
        enc = ["-c:v", "qtrle"]
    elif args.alpha == "vp9":
        enc = ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0",
               "-crf", "28", "-row-mt", "1", "-cpu-used", "5"]
    else:
        enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    cmd = [find_ffmpeg(), "-y", "-f", "rawvideo", "-pix_fmt", in_fmt, "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-"] + enc + [out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    jobs = args.jobs or (os.cpu_count() or 1)
    jobs = max(1, min(jobs, nframes))
    ctxs = (frame_ctx(i, ft, ch, nframes) for i in range(nframes))
    t_start = time.time()
    if jobs == 1:
        for k, ctx in enumerate(ctxs):
            proc.stdin.write(_render_bytes(tmpl, ctx, ALPHA))
            progress_bar(k + 1, nframes, t_start)
    else:
        # frames are independent -> render across a process pool, write in order
        with mp.Pool(jobs, initializer=_worker_init, initargs=(tmpl, ALPHA)) as pool:
            for k, buf in enumerate(pool.imap(_worker_render, ctxs, chunksize=4)):
                proc.stdin.write(buf)
                progress_bar(k + 1, nframes, t_start)
    proc.stdin.close()
    proc.wait()
    el = time.time() - t_start
    sys.stdout.write("\n")
    print(f"done: {nframes} frames in {el/60:.1f} min ({nframes/el:.1f} fps, "
          f"{jobs} job{'s' if jobs > 1 else ''}) -> {out}")


if __name__ == "__main__":
    main()
