#!/usr/bin/env python3
"""
Step 3 - Build NEW timelines in DaVinci Resolve Studio from edit_plan.json.

SAFE: this only ADDS new timelines (e.g. "AM Rough Cut v1",
"AM Highlights Stringout v1"). It never deletes or edits existing timelines,
clips or media. If a name already exists it uses v2, v3, ...

Run it either:
  - From Resolve: Workspace > Scripts > Aftermovie > resolve_build_timelines
  - From Terminal: ./aftermovie.sh build
"""
import json
import os
import sys

WORK_DIR = os.environ.get("AFTERMOVIE_DIR", os.path.expanduser("~/Desktop/Aftermovie"))
VIDEO_ONLY = os.environ.get("AFTERMOVIE_VIDEO_ONLY") == "1"


def get_resolve():
    g = globals()
    if g.get("resolve"):
        return g["resolve"]
    if g.get("bmd"):
        r = g["bmd"].scriptapp("Resolve")
        if r:
            return r
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
    fn = getattr(obj, name, None)
    if fn is None:
        return default
    try:
        res = fn(*args)
    except Exception:
        return default
    return default if res is None else res


def index_media_pool(folder, by_id, by_path):
    for clip in folder.GetClipList() or []:
        uid = call(clip, "GetUniqueId", default="") or call(clip, "GetMediaId", default="")
        if uid:
            by_id[uid] = clip
        path = clip.GetClipProperty("File Path")
        if path:
            by_path.setdefault(path, clip)
    for sub in folder.GetSubFolderList() or []:
        index_media_pool(sub, by_id, by_path)


def unique_name(project, base):
    existing = {project.GetTimelineByIndex(i).GetName()
                for i in range(1, int(project.GetTimelineCount()) + 1)}
    v = 1
    while f"{base} v{v}" in existing:
        v += 1
    return f"{base} v{v}"


def build(project, mp, spec, by_id, by_path, tl_fps):
    infos, metas, missing = [], [], 0
    for c in spec["clips"]:
        mpi = by_id.get(c.get("media_id")) or by_path.get(c["path"])
        if not mpi:
            missing += 1
            continue
        info = {"mediaPoolItem": mpi,
                "startFrame": int(c["src_start_frame"]),
                "endFrame": int(c["src_end_frame"]) - 1}
        if VIDEO_ONLY:
            info["mediaType"] = 1
        infos.append(info)
        metas.append(c)
    if not infos:
        print(f"  '{spec['name']}': no clips found in the media pool - skipped")
        return None

    name = unique_name(project, spec["name"])
    tl = mp.CreateEmptyTimeline(name)
    if not tl:
        print(f"  could not create timeline '{name}'")
        return None
    project.SetCurrentTimeline(tl)
    items = mp.AppendToTimeline(infos) or []

    # Color the video clips by section/category and leave a note marker on each.
    tl_start = call(tl, "GetStartFrame", default=0)
    def is_video(it):
        kind = call(it, "GetTrackTypeAndIndex")  # Resolve 19+
        return kind[0] == "video" if kind else True
    video_items = sorted((it for it in items if is_video(it)), key=lambda it: it.GetStart())
    seen = set()
    video_items = [it for it in video_items if not (it.GetStart() in seen or seen.add(it.GetStart()))]
    for it, c in zip(video_items, metas):
        call(it, "SetClipColor", c.get("color", "Orange"))
        if spec["name"].endswith("Stringout"):
            call(it, "AddMarker", 0, "Cream", c.get("label", ""), c.get("note", ""), 1)
    for m in spec.get("markers", []):
        frame = int(round(m["time_sec"] * tl_fps))
        call(tl, "AddMarker", frame, m.get("color", "Blue"), m.get("name", ""), m.get("note", ""), 1)

    secs = (tl.GetEndFrame() - tl_start) / tl_fps
    msg = f"  + '{name}': {len(infos)} clips, {secs:.0f}s"
    if missing:
        msg += f" ({missing} clips not found in media pool)"
    print(msg)
    return tl


def main():
    plan_path = os.path.join(WORK_DIR, "edit_plan.json")
    if not os.path.exists(plan_path):
        sys.exit(f"{plan_path} not found - run step 2 (./aftermovie.sh analyze) first.")
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)

    resolve = get_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        sys.exit("No project is open.")
    mp = project.GetMediaPool()
    by_id, by_path = {}, {}
    index_media_pool(mp.GetRootFolder(), by_id, by_path)
    tl_fps = float(project.GetSetting("timelineFrameRate") or 25)

    print(f"Building timelines in project '{project.GetName()}' (nothing existing is changed):")
    first = None
    for spec in plan["timelines"]:
        tl = build(project, mp, spec, by_id, by_path, tl_fps)
        first = first or tl
    if first:
        project.SetCurrentTimeline(first)
    print("Done. Your original timeline is untouched.")


main()
