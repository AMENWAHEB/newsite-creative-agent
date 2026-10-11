#!/usr/bin/env python3
"""
Step 1 - Export the CURRENT timeline in DaVinci Resolve Studio to JSON.

READ-ONLY: this script never changes your project, timeline or media.
It only writes ~/Desktop/Aftermovie/timeline_clips.json

Run it either:
  - From Resolve: Workspace > Scripts > Aftermovie > resolve_export_timeline
  - From Terminal: ./aftermovie.sh export
"""
import json
import os
import sys
import datetime

OUT_DIR = os.environ.get("AFTERMOVIE_DIR", os.path.expanduser("~/Desktop/Aftermovie"))


def get_resolve():
    # 1) Launched from Resolve's Scripts menu: `resolve` / `bmd` already exist.
    g = globals()
    if g.get("resolve"):
        return g["resolve"]
    if g.get("bmd"):
        r = g["bmd"].scriptapp("Resolve")
        if r:
            return r
    # 2) Launched from Terminal (needs Preferences > System > General >
    #    "External scripting using" = Local).
    mod_dir = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
    if mod_dir not in sys.path:
        sys.path.append(mod_dir)
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        sys.exit("Could not load DaVinciResolveScript. Is Resolve Studio installed?")
    r = dvr.scriptapp("Resolve")
    if not r:
        sys.exit("Resolve is not reachable. Open Resolve and set Preferences > System > "
                 "General > External scripting using = Local.")
    return r


def call(obj, name, *args, default=None):
    """Call an API method only if this Resolve version has it."""
    fn = getattr(obj, name, None)
    if fn is None:
        return default
    try:
        res = fn(*args)
    except Exception:
        return default
    return default if res is None else res


def to_float(v, default=0.0):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


DRONE_WORDS = [w.strip().lower() for w in os.environ.get(
    "AFTERMOVIE_DRONE_KEYWORDS", "drone,רחפן,dji,mavic,avata,air 3,mini 4").split(",") if w.strip()]


def find_drone_clips(folder, trail, skip_paths, out):
    """Video clips in the media pool whose bin or name looks like drone footage."""
    here = trail + [folder.GetName() or ""]
    for mpi in folder.GetClipList() or []:
        path = mpi.GetClipProperty("File Path") or ""
        kind = (mpi.GetClipProperty("Type") or "").lower()
        if not path or path in skip_paths or "video" not in kind:
            continue
        text = " / ".join(here + [mpi.GetName() or "", os.path.basename(path)]).lower()
        if any(w in text for w in DRONE_WORDS):
            out.append((mpi, " / ".join(here)))
            skip_paths.add(path)
    for sub in folder.GetSubFolderList() or []:
        find_drone_clips(sub, here, skip_paths, out)


def main():
    resolve = get_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        sys.exit("No project is open.")
    tl = project.GetCurrentTimeline()
    if not tl:
        sys.exit("No timeline is open.")

    tl_fps = to_float(tl.GetSetting("timelineFrameRate"), 25.0)
    tl_start = call(tl, "GetStartFrame", default=0)
    clips = []
    for track in range(1, int(tl.GetTrackCount("video")) + 1):
        for idx, item in enumerate(tl.GetItemListInTrack("video", track) or []):
            mpi = item.GetMediaPoolItem()
            if not mpi:
                continue  # titles, generators, adjustment clips
            path = mpi.GetClipProperty("File Path") or ""
            if not path:
                continue
            clip_fps = to_float(mpi.GetClipProperty("FPS"), tl_fps) or tl_fps
            dur_tl = int(item.GetDuration())
            src_start = call(item, "GetSourceStartFrame")
            src_end = call(item, "GetSourceEndFrame")
            if src_start is None or src_end is None:
                src_start = int(item.GetLeftOffset())
                src_end = src_start + int(round(dur_tl * clip_fps / tl_fps))
            clips.append({
                "track": track,
                "index": idx,
                "name": item.GetName(),
                "path": path,
                "media_id": call(mpi, "GetUniqueId", default="") or call(mpi, "GetMediaId", default=""),
                "clip_fps": clip_fps,
                "clip_start_tc": mpi.GetClipProperty("Start TC") or "",
                "src_start_frame": int(src_start),
                "src_end_frame": int(src_end),
                "timeline_start_frame": int(item.GetStart()) - int(tl_start),
                "timeline_duration_frames": dur_tl,
            })

    # Drone shots that live in the media pool but are not on the timeline.
    drones = []
    find_drone_clips(project.GetMediaPool().GetRootFolder(), [],
                     {c["path"] for c in clips}, drones)
    for mpi, bin_path in drones:
        fps = to_float(mpi.GetClipProperty("FPS"), tl_fps) or tl_fps
        frames = int(to_float(mpi.GetClipProperty("Frames"), 0))
        if frames <= 0:
            continue
        clips.append({
            "track": 0, "index": 0, "name": mpi.GetName(),
            "path": mpi.GetClipProperty("File Path"),
            "media_id": call(mpi, "GetUniqueId", default="") or call(mpi, "GetMediaId", default=""),
            "clip_fps": fps, "clip_start_tc": mpi.GetClipProperty("Start TC") or "",
            "src_start_frame": 0, "src_end_frame": frames,
            "timeline_start_frame": -1, "timeline_duration_frames": 0,
            "drone": True, "bin": bin_path,
        })

    clips.sort(key=lambda c: (c["timeline_start_frame"], c["track"]))
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "timeline_clips.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "project": project.GetName(),
            "timeline": tl.GetName(),
            "timeline_fps": tl_fps,
            "clips": clips,
        }, f, ensure_ascii=False, indent=2)
    total = sum(c["timeline_duration_frames"] for c in clips) / tl_fps
    print(f"Exported {len(clips) - len(drones)} clips ({total/60:.1f} min) from '{tl.GetName()}' -> {out}")
    print(f"Drone clips found in media pool: {len(drones)}")
    for mpi, bin_path in drones[:20]:
        print(f"  - {bin_path} / {mpi.GetName()}")
    if not drones:
        print("  (none - put them in a bin named 'Drone' or 'רחפן', or set AFTERMOVIE_DRONE_KEYWORDS)")


main()
