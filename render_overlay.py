"""Cycling telemetry overlay generator - vertical 9:16, transparent qtrle .mov.

Parses a Strava-style GPX (1 Hz trackpoints with lat/lon, ele, time, heart rate)
and renders an animated overlay for compositing in CapCut (no chroma key needed).

Usage examples (run from anywhere):
  python render_overlay.py --gpx "C:\\path\\ride.gpx" --start 05:53:00 --end 05:54:00
  python render_overlay.py --gpx ride.gpx --start 120 --end 300 --out clip.mov
  python render_overlay.py --gpx ride.gpx --png check.png --png-at 05:53:30

--start/--end accept local clock time HH:MM:SS (matches the on-screen clock)
or plain ride-seconds. Default output: transparent QuickTime Animation .mov
(confirmed working in CapCut desktop). --alpha green gives chroma-green MP4.

Design (locked 2026-07-08, do not change without asking):
  left stack   TIME / CALORIES / ACCELERATION / DISTANCE / CLIMBED
  bottom-left  heading-up rotating follow-map (white band -> grey far away,
               teal-green glow + up arrow marker) + running clock (HH:MM:SS)
  bottom-right speed ring (teal->green, 0-40 km/h) + pulsing HR ring
  font Bahnschrift; pure-white bold text with dark drop shadow
"""
import argparse, glob, math, os, shutil, subprocess, sys, time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageChops

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

FFMPEG = find_ffmpeg()

W, H, FPS = 1080, 1920, 30
GREEN = (0, 255, 0)
DISC = (13, 17, 23)
TRACK = (58, 65, 78)
WHITE = (255, 255, 255)
GRAY = (244, 247, 251)
DIMGRAY = (210, 216, 224)
SHADOW = (10, 13, 17)
TEAL = (0, 229, 255)
GRN = (0, 230, 118)
RED = (255, 59, 92)
ORANGE = (255, 138, 101)
LIME = (178, 255, 89)

FONT = r"C:\Windows\Fonts\bahnschrift.ttf"

ALPHA = True  # transparent output by default; --alpha green renders chroma mp4

def canvas(size):
    if ALPHA:
        return Image.new("RGBA", size, (0, 0, 0, 0))
    return Image.new("RGB", size, GREEN)

def font(size, weight=350, width=100):
    f = ImageFont.truetype(FONT, size)
    try:
        f.set_variation_by_axes([weight, width])
    except Exception:
        pass
    return f

def lerp_rgb(c1, c2, t):
    return tuple(int(round(c1[i] + (c2[i] - c1[i]) * t)) for i in range(3))

def ease_out_cubic(t):
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3

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

SPD_MAX = 40.0
HR_MAX = 190.0

# ---------------------------------------------------------------- layout
MX = 70
ROWS_Y = [150, 296, 442, 588, 734]

BIG_C = (700, 1640); BIG_R = 168; BIG_DISC = 148; BIG_AW = 13
SM_C = (968, 1716); SM_R = 84; SM_DISC = 72; SM_AW = 9

MAP_C = (190, 1540)
MAP_W2 = 120
MAP_H2 = 250
MPP = 6.0
DATE_Y = 1832

BIG_BOX = (BIG_C[0] - BIG_R - 14, BIG_C[1] - BIG_R - 14,
           BIG_C[0] + BIG_R + 14, BIG_C[1] + BIG_R + 14)
SM_BOX = (SM_C[0] - SM_R - 12, SM_C[1] - SM_R - 12,
          SM_C[0] + SM_R + 12, SM_C[1] + SM_R + 12)

F_VAL = font(60, 520, 96)
F_UNIT = font(30, 520, 96)
F_SPD = font(126, 520, 92)
F_KMH = font(30, 600, 100)
F_BPM = font(58, 520, 92)
F_BPMU = font(24, 600, 100)
F_DATE = font(30, 520, 96)

def text_sh(dr, xy, s, f, fill=WHITE, anchor="la"):
    dr.text((xy[0] + 2, xy[1] + 3), s, font=f, fill=SHADOW, anchor=anchor)
    dr.text(xy, s, font=f, fill=fill, anchor=anchor)

# ---------------------------------------------------------------- static pieces
def draw_ring_base(dr, c, r_disc, r_ring, aw, s):
    cx, cy = c[0] * s, c[1] * s
    dr.ellipse([cx - r_disc * s, cy - r_disc * s, cx + r_disc * s, cy + r_disc * s], fill=DISC)
    rr = r_ring * s
    dr.arc([cx - rr, cy - rr, cx + rr, cy + rr], 0, 360, fill=TRACK, width=aw * s)

def build_static():
    s = 2
    img = canvas((W * s, H * s))
    dr = ImageDraw.Draw(img)
    for y, c in zip(ROWS_Y, ["TIME", "CALORIES", "ACCELERATION", "DISTANCE", "CLIMBED"]):
        dr.text((MX * s + 2, (y + 64) * s + 3), c, font=font(25 * s, 600), fill=SHADOW)
        dr.text((MX * s, (y + 64) * s), c, font=font(25 * s, 600), fill=GRAY)
        dr.line([MX * s, (y + 106) * s, (MX + 150) * s, (y + 106) * s], fill=DIMGRAY, width=1 * s)
    draw_ring_base(dr, BIG_C, BIG_DISC, BIG_R, BIG_AW, s)
    draw_ring_base(dr, SM_C, SM_DISC, SM_R, SM_AW, s)
    base = img.resize((W, H), Image.LANCZOS)
    return np.asarray(base, dtype=np.uint8)

def build_bigmap():
    """Route band as union mask at MPP zoom; returns mask + per-second px coords."""
    latm = np.radians(lat.mean())
    xm = (lon - lon.min()) * math.cos(latm) * 111320.0
    ym = (lat - lat.min()) * 110540.0
    margin = int(math.hypot(MAP_W2, MAP_H2)) + 40
    px = xm / MPP + margin
    py = (ym.max() - ym) / MPP + margin
    bw = int(px.max() + margin)
    bh = int(py.max() + margin)
    s = 2
    di = np.arange(0, dist[-1], MPP)
    rx = np.interp(di, dist, px)
    ry = np.interp(di, dist, py)
    k = np.ones(15) / 15
    rx = np.convolve(rx, k, mode="same"); rx[:15] = rx[15]; rx[-15:] = rx[-16]
    ry = np.convolve(ry, k, mode="same"); ry[:15] = ry[15]; ry[-15:] = ry[-16]
    mask = Image.new("L", (bw * s, bh * s), 0)
    dm = ImageDraw.Draw(mask)
    r = 11 * s / 2
    for x, y in zip(rx * s, ry * s):
        dm.ellipse([x - r, y - r, x + r, y + r], fill=255)
    mask = mask.resize((bw, bh), Image.LANCZOS)
    return mask, np.stack([px, py], axis=1)

def grad_window():
    w, h = 2 * MAP_W2, 2 * MAP_H2
    yy, xx = np.mgrid[0:h, 0:w]
    rr = np.sqrt(((xx - MAP_W2) / MAP_W2) ** 2 + ((yy - MAP_H2) / MAP_H2) ** 2)
    t = np.clip((rr - 0.22) / 0.65, 0, 1)[..., None]
    white = np.array([255, 255, 255], dtype=float)
    grey = np.array([128, 135, 146], dtype=float)
    return Image.fromarray((white + (grey - white) * t).astype(np.uint8))

def capsule_mask():
    s = 4
    w, h = 2 * MAP_W2 * s, 2 * MAP_H2 * s
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=MAP_W2 * s, fill=255)
    return m.resize((2 * MAP_W2, 2 * MAP_H2), Image.LANCZOS)

def marker_glow(px=108):
    im = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    dd = ImageDraw.Draw(im)
    c = px / 2
    for r in range(int(c), 0, -1):
        t = r / c
        col = lerp_rgb(TEAL, GRN, t)
        a = int(250 * (1 - t) ** 1.1) if t > 0.45 else 250
        dd.ellipse([c - r, c - r, c + r, c + r], fill=col + (a,))
    return im

def marker_arrow(px=128):
    im = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    dd = ImageDraw.Draw(im)
    u = px / 128.0
    pts = [(64 * u, 16 * u), (104 * u, 110 * u), (64 * u, 86 * u), (24 * u, 110 * u)]
    dd.polygon(pts, fill=(245, 248, 252, 255), outline=(30, 36, 44, 255), width=int(6 * u))
    return im

def heart_sprite(px=256):
    im = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    dd = ImageDraw.Draw(im)
    u = px / 32.0
    dd.ellipse([4*u, 5*u, 16.5*u, 17.5*u], fill=RED + (255,))
    dd.ellipse([15.5*u, 5*u, 28*u, 17.5*u], fill=RED + (255,))
    dd.polygon([(4.6*u, 13.4*u), (16*u, 29*u), (27.4*u, 13.4*u), (16*u, 17*u)], fill=RED + (255,))
    dd.polygon([(5.5*u, 12*u), (16*u, 27.5*u), (26.5*u, 12*u)], fill=RED + (255,))
    return im

MGLOW = marker_glow()
MARROW = marker_arrow()
HEART = heart_sprite()

def ring_patch(box, c, r_disc, r_ring, aw, frac, col1, col2, s=2):
    pw, ph = box[2] - box[0], box[3] - box[1]
    im = canvas((pw * s, ph * s))
    dr = ImageDraw.Draw(im)
    cc = (c[0] - box[0], c[1] - box[1])
    draw_ring_base(dr, cc, r_disc, r_ring, aw, s)
    cx, cy, rr = cc[0] * s, cc[1] * s, r_ring * s
    frac = min(max(frac, 0.0), 1.0)
    if frac > 0.01:
        sweep = 360 * frac
        nseg = max(2, int(48 * frac))
        for i in range(nseg):
            t0, t1 = i / nseg, (i + 1) / nseg
            dr.arc([cx - rr, cy - rr, cx + rr, cy + rr],
                   -90 + sweep * t0 - 0.4, -90 + sweep * t1 + 0.4,
                   fill=lerp_rgb(col1, col2, t1), width=aw * s)
        ang = math.radians(-90 + sweep)
        tx, ty = cx + rr * math.cos(ang), cy + rr * math.sin(ang)
        dr.ellipse([tx - aw * s * 0.55, ty - aw * s * 0.55,
                    tx + aw * s * 0.55, ty + aw * s * 0.55], fill=(240, 252, 255))
    return im.resize((pw, ph), Image.LANCZOS)

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
    ap.add_argument("--gpx", required=True, help="path to .gpx file")
    ap.add_argument("--start", default=None, help="clock HH:MM:SS or ride-seconds")
    ap.add_argument("--end", default=None, help="clock HH:MM:SS or ride-seconds")
    ap.add_argument("--out", default=None, help="output path (.mov)")
    ap.add_argument("--png", default=None, help="render single frame to this PNG")
    ap.add_argument("--png-at", default=None, help="time of the PNG frame")
    ap.add_argument("--alpha", choices=["qtrle", "prores", "vp9", "green"], default="qtrle",
                    help="qtrle = transparent .mov for CapCut (default)")
    ap.add_argument("--no-intro", action="store_true",
                    help="skip slide-in intro (auto-skipped when start > 0)")
    ap.add_argument("--tz", type=float, default=7.0, help="UTC offset hours (default WIB +7)")
    ap.add_argument("--weight", type=float, default=70.0, help="rider kg for calories")
    ap.add_argument("--age", type=float, default=30.0, help="rider age for calories")
    args = ap.parse_args()

    global ALPHA
    ALPHA = args.alpha != "green"

    load_gpx(args.gpx, args.tz, args.weight, args.age)

    start_sec = parse_when(args.start) or 0.0
    end_sec = parse_when(args.end)
    if end_sec is None:
        end_sec = T - 1
    start_sec = max(0.0, min(start_sec, T - 2))
    end_sec = max(start_sec + 1, min(end_sec, T - 1))

    base = build_static()
    bigmask, ptsxy = build_bigmap()
    MASK = capsule_mask()
    GRADWIN = grad_window()

    # per-second heading (unwrapped, smoothed) for heading-up map rotation
    head = np.zeros(T)
    hw = 3
    last = 0.0
    for i in range(T):
        a, b = max(0, i - hw), min(T - 1, i + hw)
        vx, vy = ptsxy[b, 0] - ptsxy[a, 0], ptsxy[b, 1] - ptsxy[a, 1]
        if math.hypot(vx, vy) * MPP > 2.0:
            last = math.degrees(math.atan2(vy, vx))
        head[i] = last
    head = np.degrees(np.unwrap(np.radians(head)))
    head = np.convolve(head, np.ones(9) / 9, mode="same")
    head[:9] = head[9]; head[-9:] = head[-10]

    f0, f1 = int(start_sec * FPS), int(end_sec * FPS)
    nframes = f1 - f0

    ft = np.arange(f0, f1) / FPS
    fspd = np.interp(ft, secs, spd)
    fhr = np.interp(ft, secs, hr)
    fdist = np.interp(ft, secs, dist)
    fcal = np.interp(ft, secs, cal)
    facc = np.interp(ft, secs, acc)
    fclimb = np.interp(ft, secs, climb)
    fx = np.interp(ft, secs, ptsxy[:, 0])
    fy = np.interp(ft, secs, ptsxy[:, 1])
    fhead = np.interp(ft, secs, head)
    all_t = np.arange(0, T, 1.0 / FPS)
    all_phase = np.cumsum(np.interp(all_t, secs, hr) / 60.0 / FPS)
    fphase = np.interp(ft, all_t, all_phase)

    def val_unit(dr, x, y, val, unit):
        text_sh(dr, (x, y), val, F_VAL)
        vw = dr.textlength(val, font=F_VAL)
        text_sh(dr, (x + vw + 10, y + 26), unit, F_UNIT, fill=GRAY)

    def render_frame(i):
        img = Image.fromarray(base.copy())
        dr = ImageDraw.Draw(img)

        tsec = int(ft[i])
        val_unit(dr, MX, ROWS_Y[0], f"{tsec // 3600}:{tsec % 3600 // 60:02d}:{tsec % 60:02d}", "")
        val_unit(dr, MX, ROWS_Y[1], f"{fcal[i]:.0f}", "Cal")
        a_g = facc[i]
        val_unit(dr, MX, ROWS_Y[2], f"{a_g:+.2f}" if abs(a_g) >= 0.005 else "0.00", "G")
        val_unit(dr, MX, ROWS_Y[3], f"{fdist[i] / 1000:.1f}", "Km")
        val_unit(dr, MX, ROWS_Y[4], f"{fclimb[i]:.0f}", "M")

        bp = ring_patch(BIG_BOX, BIG_C, BIG_DISC, BIG_R, BIG_AW,
                        fspd[i] / SPD_MAX, TEAL, GRN)
        img.paste(bp, (BIG_BOX[0], BIG_BOX[1]), bp if ALPHA else None)
        dr.text((BIG_C[0], BIG_C[1] - 22), f"{fspd[i]:.0f}", font=F_SPD, fill=WHITE, anchor="mm")
        dr.text((BIG_C[0], BIG_C[1] + 74), "KM/H", font=F_KMH, fill=GRAY, anchor="mm")

        sp = ring_patch(SM_BOX, SM_C, SM_DISC, SM_R, SM_AW,
                        fhr[i] / HR_MAX, RED, ORANGE)
        img.paste(sp, (SM_BOX[0], SM_BOX[1]), sp if ALPHA else None)
        dr.text((SM_C[0], SM_C[1] + 4), f"{fhr[i]:.0f}", font=F_BPM, fill=WHITE, anchor="mm")
        dr.text((SM_C[0], SM_C[1] + 40), "BPM", font=F_BPMU, fill=GRAY, anchor="mm")
        p = fphase[i] % 1.0
        hs = int(30 * (1.0 + 0.16 * math.exp(-5.0 * p)))
        hh = HEART.resize((hs, hs), Image.LANCZOS)
        img.paste(hh, (SM_C[0] - hs // 2, SM_C[1] - 52 - hs // 2), hh)

        # heading-up follow-map
        cx, cy = fx[i], fy[i]
        R2 = int(math.hypot(MAP_W2, MAP_H2)) + 6
        rot_deg = 90.0 + fhead[i]
        winb = bigmask.crop((int(cx) - R2, int(cy) - R2, int(cx) + R2, int(cy) + R2))
        winr = winb.rotate(rot_deg, resample=Image.BILINEAR)
        wm = winr.crop((R2 - MAP_W2, R2 - MAP_H2, R2 + MAP_W2, R2 + MAP_H2))
        img.paste(GRADWIN, (MAP_C[0] - MAP_W2, MAP_C[1] - MAP_H2),
                  ImageChops.multiply(wm, MASK))
        a = math.radians(rot_deg)
        dxv = ptsxy[0, 0] - cx
        dyv = ptsxy[0, 1] - cy
        rxv = dxv * math.cos(a) + dyv * math.sin(a)
        ryv = -dxv * math.sin(a) + dyv * math.cos(a)
        if ((rxv / (MAP_W2 - 12)) ** 2 + (ryv / (MAP_H2 - 12)) ** 2) < 1:
            dr.ellipse([MAP_C[0] + rxv - 6, MAP_C[1] + ryv - 6,
                        MAP_C[0] + rxv + 6, MAP_C[1] + ryv + 6],
                       fill=LIME, outline=(30, 36, 44), width=2)
        img.paste(MGLOW, (MAP_C[0] - MGLOW.width // 2, MAP_C[1] - MGLOW.height // 2), MGLOW)
        arr_s = MARROW.resize((44, 44), Image.LANCZOS)
        img.paste(arr_s, (MAP_C[0] - 22, MAP_C[1] - 22), arr_s)

        clk = START_SEC_LOCAL + tsec
        text_sh(dr, (MX, DATE_Y),
                f"{clk // 3600 % 24:02d}:{clk % 3600 // 60:02d}:{clk % 60:02d}",
                F_DATE, fill=GRAY)
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
        out = os.path.join(os.path.dirname(os.path.abspath(args.gpx)),
                           f"{stem}_overlay_{tag}{ext}")

    # intro only when the clip starts at the ride start
    GROUPS = [
        ((MX - 20, ROWS_Y[0] - 20, MX + 320, ROWS_Y[4] + 110), (-1, 0)),
        ((MAP_C[0] - MAP_W2 - 30, MAP_C[1] - MAP_H2 - 30,
          MAP_C[0] + MAP_W2 + 30, DATE_Y + 60), (-1, 0)),
        ((BIG_BOX[0], BIG_BOX[1], SM_BOX[2], max(BIG_BOX[3], SM_BOX[3]) + 20), (0, 1)),
    ]
    INTRO_F = int(1.5 * FPS) if (f0 == 0 and not args.no_intro) else 0

    def with_intro(img, k):
        out_img = canvas((W, H))
        for gi, (box, (dx, dy)) in enumerate(GROUPS):
            t0 = gi * 0.10
            tt = ease_out_cubic((k / FPS - t0) / 0.7)
            if tt <= 0:
                continue
            x0, y0, x1, y1 = [int(max(0, min(v, W if a % 2 == 0 else H)))
                              for a, v in enumerate(box)]
            region = img.crop((x0, y0, x1, y1))
            offx = int((1 - tt) * (-(x1) if dx < 0 else (W - x0) if dx > 0 else 0))
            offy = int((1 - tt) * ((H - y0) if dy > 0 else 0))
            out_img.paste(region, (x0 + offx, y0 + offy))
        return out_img

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
    cmd = [FFMPEG, "-y", "-f", "rawvideo", "-pix_fmt", in_fmt, "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-"] + enc + [out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t_start = time.time()
    for i in range(nframes):
        img = render_frame(i)
        if i < INTRO_F:
            img = with_intro(img, i)
        proc.stdin.write(img.tobytes())
        if i % 1800 == 0 and i:
            el = time.time() - t_start
            print(f"frame {i}/{nframes}  {i/el:.1f} fps  "
                  f"ETA {(el/i*(nframes-i))/60:.1f} min", flush=True)
    proc.stdin.close()
    proc.wait()
    el = time.time() - t_start
    print(f"done: {nframes} frames in {el/60:.1f} min ({nframes/el:.1f} fps) -> {out}")

if __name__ == "__main__":
    main()
