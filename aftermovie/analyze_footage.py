#!/usr/bin/env python3
"""
Step 2 - Analyze the footage and plan an aftermovie rough cut.

READ-ONLY on your media: ffmpeg only reads the clips.
Input : ~/Desktop/Aftermovie/timeline_clips.json  (from step 1)
        or --folder /path/to/footage  (analyze whole files instead)
Output: ~/Desktop/Aftermovie/highlights.csv   ranked list of the best moments
        ~/Desktop/Aftermovie/edit_plan.json   timelines for step 3 to build

For every few seconds of footage it measures:
  motion     - how much is moving (dancing, hands up, lights)
  audio      - how loud the room is (screams, crowd singing, drops)
  sharpness  - in focus vs. blurry
  brightness - too dark / blown out
and turns them into an "energy" and a "quality" score.

Requires: ffmpeg (brew install ffmpeg) and numpy (pip3 install numpy).
"""
import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

DEFAULT_DIR = os.environ.get("AFTERMOVIE_DIR", os.path.expanduser("~/Desktop/Aftermovie"))
VIDEO_EXT = {".mp4", ".mov", ".mxf", ".m4v", ".avi", ".mts"}
W, H = 256, 144
AUDIO_SR = 16000

# Rough-cut structure: (section, share of total length, clip length in s, score key, color)
SECTIONS = [
    ("Intro", 0.15, 3.0, "mood", "Blue"),
    ("Build", 0.30, 2.0, "build", "Yellow"),
    ("Peak", 0.45, 1.0, "peak", "Orange"),
    ("Outro", 0.10, 2.5, "overall", "Purple"),
]


# ─── decoding ────────────────────────────────────────────────────────────────

def run_ffmpeg(args):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *args],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout


def decode_video(path, start, dur, fps, hwaccel):
    vf = f"fps={fps},scale={W}:{H}:flags=area,format=gray"
    tail = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", path, "-an",
            "-vf", vf, "-f", "rawvideo", "-"]
    attempts = [["-hwaccel", "videotoolbox"] + tail, tail] if hwaccel else [tail]
    for a in attempts:
        code, out = run_ffmpeg(a)
        n = len(out) // (W * H)
        if code == 0 and n > 0:
            return np.frombuffer(out[: n * W * H], np.uint8).reshape(n, H, W)
    return np.zeros((0, H, W), np.uint8)


def decode_audio(path, start, dur):
    code, out = run_ffmpeg(["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", path, "-vn",
                            "-ac", "1", "-ar", str(AUDIO_SR), "-f", "s16le", "-"])
    if code != 0 or not out:
        return np.zeros(0, np.float32)
    return np.frombuffer(out[: len(out) // 2 * 2], np.int16).astype(np.float32) / 32768.0


def probe_duration(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path], stdout=subprocess.PIPE, text=True)
    return float(p.stdout.strip() or 0)


def probe_fps(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=r_frame_rate", "-of", "default=nw=1:nk=1", path],
                       stdout=subprocess.PIPE, text=True)
    try:
        n, d = p.stdout.strip().split("/")
        return float(n) / float(d)
    except ValueError:
        return 25.0


# ─── per-sample features ─────────────────────────────────────────────────────

def segment_features(seg, fps, hwaccel):
    """Return dict of per-sample arrays (one sample = 1/fps seconds)."""
    frames = decode_video(seg["path"], seg["start"], seg["dur"], fps, hwaccel)
    n = len(frames)
    if n == 0:
        return None
    f = frames.astype(np.float32)
    brightness = f.mean(axis=(1, 2)) / 255.0
    lap = (4 * f[:, 1:-1, 1:-1] - f[:, :-2, 1:-1] - f[:, 2:, 1:-1]
           - f[:, 1:-1, :-2] - f[:, 1:-1, 2:])
    sharpness = lap.var(axis=(1, 2))
    motion = np.zeros(n, np.float32)
    if n > 1:
        motion[1:] = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))
        motion[0] = motion[1]

    pcm = decode_audio(seg["path"], seg["start"], seg["dur"])
    hop = AUDIO_SR / fps
    audio = np.full(n, -90.0, np.float32)
    for i in range(n):
        chunk = pcm[int(i * hop): int((i + 1) * hop)]
        if len(chunk):
            audio[i] = 20 * math.log10(float(np.sqrt(np.mean(chunk ** 2))) + 1e-6)
    return {"brightness": brightness, "sharpness": sharpness, "motion": motion, "audio": audio}


# ─── windows & scoring ───────────────────────────────────────────────────────

def make_windows(seg, feats, fps, win, step):
    n = len(feats["motion"])
    wn = max(1, int(round(win * fps)))
    st = max(1, int(round(step * fps)))
    starts = list(range(0, max(1, n - wn + 1), st))
    out = []
    for s in starts:
        e = min(n, s + wn)
        out.append({
            "seg": seg["id"],
            "t0": s / fps,  # seconds from segment start
            "t1": e / fps,
            "path": seg["path"],
            "src0": seg["start"] + s / fps,  # seconds in the source file
            "src1": seg["start"] + e / fps,
            "motion": float(np.mean(feats["motion"][s:e])),
            "audio": float(np.mean(feats["audio"][s:e])),
            "sharpness": float(np.median(feats["sharpness"][s:e])),
            "brightness": float(np.mean(feats["brightness"][s:e])),
        })
    return out


def rank01(values):
    v = np.asarray(values, np.float64)
    if len(v) < 2:
        return np.full(len(v), 0.5)
    order = v.argsort(kind="stable")
    r = np.empty(len(v))
    r[order] = np.arange(len(v))
    return r / (len(v) - 1)


def score_windows(wins):
    m = rank01([w["motion"] for w in wins])
    a = rank01([w["audio"] for w in wins])
    s = rank01([w["sharpness"] for w in wins])
    b = rank01([w["brightness"] for w in wins])
    for i, w in enumerate(wins):
        q = 0.65 * s[i] + 0.35 * b[i]
        if w["brightness"] < 0.04:      # basically black
            q *= 0.3
        elif w["brightness"] < 0.08:    # very dark
            q *= 0.7
        elif w["brightness"] > 0.85:    # blown out
            q *= 0.7
        energy = 0.55 * m[i] + 0.45 * a[i]
        w["quality"] = q
        w["energy"] = energy
        w["peak"] = 0.6 * energy + 0.4 * q
        w["build"] = 0.5 * q + 0.5 * (1 - abs(energy - 0.55) * 2)
        w["mood"] = 0.7 * q + 0.3 * (1 - energy)
        w["overall"] = 0.5 * energy + 0.5 * q
        w["category"] = "peak" if energy >= 0.66 else ("build" if energy >= 0.33 else "mood")


def overlaps(w, ranges, gap):
    return any(w["src0"] < r1 + gap and w["src1"] > r0 - gap for r0, r1 in ranges)


def best_per_segment(wins, key, per_seg, min_gap):
    """Best windows, at most `per_seg` per segment, never the same source moment twice."""
    count, ranges, out = {}, {}, []
    for w in sorted(wins, key=lambda w: -w[key]):
        if count.get(w["seg"], 0) >= per_seg or overlaps(w, ranges.get(w["path"], []), min_gap):
            continue
        out.append(w)
        count[w["seg"]] = count.get(w["seg"], 0) + 1
        ranges.setdefault(w["path"], []).append((w["src0"], w["src1"]))
    return out


# ─── timecode helpers ────────────────────────────────────────────────────────

def tc_to_frames(tc, fps):
    try:
        hh, mm, ss, ff = [int(x) for x in tc.replace(";", ":").split(":")]
    except (ValueError, AttributeError):
        return 0
    nom = int(round(fps))
    return ((hh * 60 + mm) * 60 + ss) * nom + ff


def frames_to_tc(frames, fps):
    nom = max(1, int(round(fps)))
    frames = int(frames)
    ff = frames % nom
    s = frames // nom
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{ff:02d}"


# ─── edit plan ───────────────────────────────────────────────────────────────

def clip_from_window(w, seg, length, label, color, note):
    """Center a clip of `length` seconds on the window, clamped to the segment."""
    center = (w["t0"] + w["t1"]) / 2
    length = min(length, seg["dur"])
    t0 = min(max(0.0, center - length / 2), max(0.0, seg["dur"] - length))
    fps = seg["clip_fps"]
    start = seg["src_start_frame"] + int(round(t0 * fps))
    n = max(1, int(round(length * fps)))
    return {
        "path": seg["path"], "media_id": seg.get("media_id", ""), "name": seg["name"],
        "clip_fps": fps, "src_start_frame": start, "src_end_frame": start + n,
        "seconds": n / fps, "label": label, "color": color, "note": note,
        "order": (seg["order"], start),
    }


def build_rough_cut(wins, segs, target):
    used_wins, seg_uses, used_ranges = set(), {}, {}
    timeline, markers, t = [], [], 0.0
    for name, share, clip_len, key, color in SECTIONS:
        count = max(1, int(round(target * share / clip_len)))
        pool = [w for w in wins if id(w) not in used_wins]
        preferred = [w for w in pool if key == "overall" or w["category"] == key]
        pref_ids = {id(w) for w in preferred}
        ranked = sorted(preferred, key=lambda w: -w[key]) + \
            sorted([w for w in pool if id(w) not in pref_ids], key=lambda w: -w[key])
        chosen, seg_in_section = [], set()
        for w in ranked:
            if len(chosen) >= count:
                break
            if w["seg"] in seg_in_section or seg_uses.get(w["seg"], 0) >= 2:
                continue
            if w["quality"] < 0.15:
                continue  # black / unusable
            if overlaps(w, used_ranges.get(w["path"], []), 1.0):
                continue  # same moment already in the cut
            if w[key] < 0.35 and len(chosen) >= count // 2:
                break  # don't pad the cut with junk
            chosen.append(w)
            seg_in_section.add(w["seg"])
            seg_uses[w["seg"]] = seg_uses.get(w["seg"], 0) + 1
            used_wins.add(id(w))
            used_ranges.setdefault(w["path"], []).append((w["src0"], w["src1"]))
        clips = []
        for w in chosen:
            seg = segs[w["seg"]]
            clips.append(clip_from_window(
                w, seg, clip_len, name, color,
                f"{name} | energy {w['energy']:.2f} quality {w['quality']:.2f}"))
        clips.sort(key=lambda c: c["order"])  # keep the night's chronology
        if clips:
            markers.append({"time_sec": t, "color": color, "name": name.upper(),
                            "note": f"{len(clips)} clips, ~{clip_len}s each"})
        for c in clips:
            timeline.append(c)
            t += c["seconds"]
    return timeline, markers


def build_stringout(highlights, segs, limit):
    out = []
    for i, w in enumerate(highlights[:limit], 1):
        seg = segs[w["seg"]]
        color = {"peak": "Orange", "build": "Yellow", "mood": "Blue"}[w["category"]]
        c = clip_from_window(w, seg, w["t1"] - w["t0"], f"#{i}", color,
                             f"#{i} {w['category']} | score {w['overall']:.2f}")
        out.append(c)
    return out


# ─── main ────────────────────────────────────────────────────────────────────

def load_segments(args):
    segs = []
    if args.folder:
        files = sorted(os.path.join(r, f) for r, _, fs in os.walk(args.folder) for f in fs
                       if os.path.splitext(f)[1].lower() in VIDEO_EXT and not f.startswith("."))
        for i, p in enumerate(files):
            fps = probe_fps(p)
            dur = probe_duration(p)
            segs.append({"path": p, "name": os.path.basename(p), "media_id": "",
                         "clip_fps": fps, "clip_start_tc": "", "src_start_frame": 0,
                         "src_end_frame": int(dur * fps), "order": i})
        source = args.folder
    else:
        src = args.timeline or os.path.join(args.dir, "timeline_clips.json")
        if not os.path.exists(src):
            sys.exit(f"{src} not found - run step 1 (./aftermovie.sh export) first, or use --folder.")
        with open(src, encoding="utf-8") as f:
            data = json.load(f)
        for c in data["clips"]:
            segs.append(dict(c, order=c["timeline_start_frame"]))
        source = f"{data.get('project')} / {data.get('timeline')}"
    for i, s in enumerate(segs):
        s["id"] = i
        s["start"] = s["src_start_frame"] / s["clip_fps"]
        s["dur"] = max(0.0, (s["src_end_frame"] - s["src_start_frame"]) / s["clip_fps"])
    return [s for s in segs if s["dur"] > 0.3], source


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=DEFAULT_DIR, help="work folder (default ~/Desktop/Aftermovie)")
    ap.add_argument("--timeline", help="timeline_clips.json path (default: <dir>/timeline_clips.json)")
    ap.add_argument("--folder", help="analyze every video file in this folder instead of a timeline")
    ap.add_argument("--length", type=float, default=180, help="rough cut length in seconds (default 180)")
    ap.add_argument("--stringout", type=int, default=150, help="how many highlights in the stringout")
    ap.add_argument("--fps", type=float, default=4, help="analysis samples per second (default 4)")
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) // 2))
    ap.add_argument("--no-hwaccel", action="store_true", help="disable VideoToolbox decoding")
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found. Install with: brew install ffmpeg")

    segs, source = load_segments(args)
    if not segs:
        sys.exit("No clips to analyze.")
    total = sum(s["dur"] for s in segs)
    print(f"Analyzing {len(segs)} clips, {total/60:.1f} min of footage, {args.workers} workers...")

    wins, done = [], 0
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(segment_features, s, args.fps, not args.no_hwaccel): s for s in segs}
        for fut in as_completed(futs):
            seg = futs[fut]
            done += 1
            try:
                feats = fut.result()
            except Exception as e:  # keep going on a bad file
                feats = None
                print(f"  ! {seg['name']}: {e}")
            if feats is None:
                print(f"  ! could not decode {seg['name']} - skipped")
            else:
                wins += make_windows(seg, feats, args.fps, win=2.0, step=0.5)
            if done % 10 == 0 or done == len(segs):
                print(f"  {done}/{len(segs)} clips")
    if not wins:
        sys.exit("Nothing could be decoded.")

    score_windows(wins)
    usable = [w for w in wins if w["quality"] >= 0.15]
    highlights = sorted(best_per_segment(usable, "overall", per_seg=3, min_gap=1.0),
                        key=lambda w: -w["overall"])

    os.makedirs(args.dir, exist_ok=True)
    csv_path = os.path.join(args.dir, "highlights.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow(["rank", "category", "score", "energy", "quality", "clip", "source_tc_in",
                     "seconds", "brightness", "loudness_db", "file"])
        for i, w in enumerate(highlights, 1):
            s = segs[w["seg"]]
            tc0 = tc_to_frames(s.get("clip_start_tc", ""), s["clip_fps"])
            src = s["src_start_frame"] + int(w["t0"] * s["clip_fps"])
            wr.writerow([i, w["category"], f"{w['overall']:.3f}", f"{w['energy']:.3f}",
                         f"{w['quality']:.3f}", s["name"], frames_to_tc(tc0 + src, s["clip_fps"]),
                         f"{w['t1'] - w['t0']:.1f}", f"{w['brightness']:.2f}",
                         f"{w['audio']:.1f}", s["path"]])

    rough, markers = build_rough_cut(wins, segs, args.length)
    stringout = build_stringout(highlights, segs, args.stringout)
    plan = {"source": source, "timelines": [
        {"name": "AM Rough Cut", "clips": rough, "markers": markers},
        {"name": "AM Highlights Stringout", "clips": stringout, "markers": []},
    ]}
    for tl in plan["timelines"]:
        for c in tl["clips"]:
            c.pop("order", None)
    plan_path = os.path.join(args.dir, "edit_plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    cats = {k: sum(1 for w in highlights if w["category"] == k) for k in ("peak", "build", "mood")}
    print(f"\nTop moments: {len(highlights)}  (peak {cats['peak']}, build {cats['build']}, mood {cats['mood']})")
    print(f"Rough cut  : {len(rough)} clips, {sum(c['seconds'] for c in rough):.0f}s")
    print(f"Stringout  : {len(stringout)} clips")
    print(f"-> {csv_path}\n-> {plan_path}\nNext: ./aftermovie.sh build")


if __name__ == "__main__":
    main()
