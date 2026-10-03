#!/usr/bin/env python3
"""Series rotation for PiSignage.

PiSignage restarts local-folder playback from the first file whenever the
playlist reloads. This tool keeps a folder of numbered symlinks whose first
entry is always the next episode to play, so each day carries on from where
the previous one stopped.

The folder is rebuilt whenever PiSignage turns the TV off (seen in its log)
and at boot. Rebuilding while episodes play would
make PiSignage jump, because it keeps its place by position in the folder.

Commands:
  track    Record which episode the player has open and rebuild when the
           PiSignage turns the TV off (run every minute).
  rebuild  Regenerate the symlink folder(s) starting at the resume point.
  status   Show the tracked episode and resume point for each show.
"""

import argparse
import configparser
import fcntl
import json
import logging
import logging.handlers
import os
import re
import shutil
import subprocess
import sys
import time
from glob import glob

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(BASE_DIR, "rotation.conf")
VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".mpg", ".mpeg", ".ts"}
# An episode counts as finished if it was seen playing for at least its length
# minus this margin. The tracker samples once a minute, so it under-counts.
COMPLETION_MARGIN_SECONDS = 150
TV_OFF_MARKER = "**** turning tv OFF ****"
SCHEDULE_RE = re.compile(r"Schedule is changed to playlist (.+?)\s*$")
NO_OFF_WARNING_SECONDS = 36 * 3600
FIRST_READ_BYTES = 256 * 1024

log = logging.getLogger("rotation")


def natural_key(text):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def path_key(path):
    return [natural_key(part) for part in path.split(os.sep)]


class Show:
    def __init__(self, name, source_glob, output_dir, state_dir):
        self.name = name
        self.source_glob = source_glob
        self.output_dir = output_dir.rstrip("/")
        self.state_file = os.path.join(state_dir, f"{name}.json")

    def episodes(self):
        folders = sorted((d for d in glob(self.source_glob) if os.path.isdir(d)), key=natural_key)
        result = []
        for folder in folders:
            files = []
            for entry in os.listdir(folder):
                if entry.startswith("."):
                    continue
                path = os.path.join(folder, entry)
                if os.path.splitext(entry)[1].lower() in VIDEO_EXTENSIONS and os.path.isfile(path):
                    files.append(os.path.realpath(path))
            result.extend(sorted(files, key=lambda p: natural_key(os.path.basename(p))))
        return result

    def load_state(self):
        return read_json(self.state_file)

    def save_state(self, state):
        write_json(self.state_file, state)


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


def uptime_clock():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def load_config(path):
    parser = configparser.ConfigParser()
    if not parser.read(path):
        raise SystemExit(f"Config file not found: {path}")
    settings = dict(parser["settings"]) if parser.has_section("settings") else {}
    state_dir = settings.setdefault("state_dir", os.path.join(BASE_DIR, "state"))
    shows = []
    for section in parser.sections():
        if section == "settings":
            continue
        shows.append(Show(section, parser[section]["source_glob"], parser[section]["output_dir"], state_dir))
    return settings, shows


def setup_logging(settings):
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    log.addHandler(stream)
    log_file = settings.get("log_file", os.path.join(BASE_DIR, "rotation.log"))
    if log_file:
        handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=512 * 1024, backupCount=2)
        handler.setFormatter(fmt)
        log.addHandler(handler)


def open_files(proc_root="/proc"):
    """Real paths of every file currently open by processes we can inspect."""
    found = set()
    for pid in os.listdir(proc_root):
        if not pid.isdigit():
            continue
        fd_dir = os.path.join(proc_root, pid, "fd")
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target.startswith("/"):
                found.add(os.path.realpath(target))
    return found


def media_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return float(out)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def track(shows, now=None, opened=None, clock=None, boot=None):
    now = int(now if now is not None else time.time())
    clock = clock if clock is not None else uptime_clock()
    boot = boot if boot is not None else boot_id()
    opened = opened if opened is not None else open_files()
    for show in shows:
        playing = [p for p in show.episodes() if p in opened]
        if not playing:
            continue
        state = show.load_state()
        current = state.get("current")
        if current in playing and state.get("boot_id") == boot:
            state.update(last_seen=now, last_seen_clock=clock)
        else:
            episode = current if current in playing else playing[0]
            if current and current != episode:
                state["previous"] = current
            state.update(current=episode, started=now, last_seen=now,
                         started_clock=clock, last_seen_clock=clock, boot_id=boot)
            if current != episode:
                log.info("%s: now playing %s", show.name, os.path.basename(episode))
        show.save_state(state)


def scan_schedule(log_path, schedule_file):
    """Read new PiSignage log lines. Returns True if PiSignage turned the TV off."""
    sched = read_json(schedule_file)
    try:
        size = os.path.getsize(log_path)
    except OSError as e:
        log.warning("Cannot read PiSignage log %s: %s", log_path, e)
        return False
    first_run = "offset" not in sched
    offset = sched.get("offset", 0)
    if first_run:
        offset = max(0, size - FIRST_READ_BYTES)
    elif size < offset:
        offset = 0
    with open(log_path, "rb") as f:
        f.seek(offset)
        chunk = f.read()
    end = offset + len(chunk)
    if not chunk.endswith(b"\n"):
        cut = chunk.rfind(b"\n") + 1
        chunk, end = chunk[:cut], offset + cut

    turned_off = False
    current = sched.get("playlist")
    for raw in chunk.decode("utf-8", "replace").splitlines():
        if TV_OFF_MARKER in raw:
            if not first_run:
                turned_off = True
            continue
        match = SCHEDULE_RE.search(raw)
        if not match:
            continue
        playlist = match.group(1)
        if playlist != current:
            log.info("PiSignage schedule: %s", playlist)
        current = playlist
    if turned_off:
        sched["last_off_at"] = int(time.time())
    sched.update(offset=end, playlist=current)
    write_json(schedule_file, sched)
    return turned_off


def warn_if_no_off_events(shows, schedule_file, now=None):
    now = now if now is not None else time.time()
    sched = read_json(schedule_file)
    since = sched.get("last_off_at") or sched.setdefault("tracking_since", int(now))
    if now - since < NO_OFF_WARNING_SECONDS:
        write_json(schedule_file, sched)
        return
    if now - sched.get("last_warning_at", 0) < 24 * 3600:
        return
    if any(now - s.load_state().get("last_seen", 0) < 600 for s in shows):
        log.warning("No TV-off seen in PiSignage's log for %.0f hours while episodes are playing; "
                    "the episode folder is only being rebuilt at boot. Check that PiSignage still "
                    "logs '%s'.", (now - since) / 3600, TV_OFF_MARKER)
        sched["last_warning_at"] = int(now)
    write_json(schedule_file, sched)


def resume_index(episodes, state, duration_fn=media_duration):
    current = state.get("current")
    if not current or not episodes:
        return 0
    if current in episodes:
        index = episodes.index(current)
        if "started_clock" in state:
            watched = state.get("last_seen_clock", 0) - state["started_clock"]
        else:
            watched = state.get("last_seen", 0) - state.get("started", 0)
        duration = duration_fn(current)
        if duration and watched >= duration - COMPLETION_MARGIN_SECONDS:
            return (index + 1) % len(episodes)
        return index
    # The tracked episode was removed or renamed: carry on from the next one in sort order.
    key = path_key(current)
    for i, path in enumerate(episodes):
        if path_key(path) > key:
            return i
    return 0


def link_name(position, path):
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(path)).strip("_")
    return f"{position:04d}_{base}"


def write_links(output_dir, ordered):
    parent = os.path.dirname(output_dir)
    os.makedirs(parent, exist_ok=True)
    name = os.path.basename(output_dir)
    staging = os.path.join(parent, f".{name}.new")
    old = os.path.join(parent, f".{name}.old")
    for leftover in (staging, old):
        shutil.rmtree(leftover, ignore_errors=True)
    os.mkdir(staging)
    for position, path in enumerate(ordered, start=1):
        os.symlink(path, os.path.join(staging, link_name(position, path)))
    if os.path.lexists(output_dir):
        os.rename(output_dir, old)
    os.rename(staging, output_dir)
    shutil.rmtree(old, ignore_errors=True)


def rebuild(shows, duration_fn=media_duration):
    rebuilt = 0
    for show in shows:
        episodes = show.episodes()
        if not episodes:
            log.warning("%s: no episodes found at %s; leaving %s unchanged",
                        show.name, show.source_glob, show.output_dir)
            continue
        state = show.load_state()
        start = resume_index(episodes, state, duration_fn)
        ordered = episodes[start:] + episodes[:start]
        write_links(show.output_dir, ordered)
        state.update(rebuilt_at=int(time.time()), first=ordered[0], episode_count=len(ordered))
        show.save_state(state)
        log.info("%s: rebuilt %d links, starting at %s", show.name, len(ordered), os.path.basename(ordered[0]))
        rebuilt += 1
    return rebuilt


def status(shows, schedule_file):
    sched = read_json(schedule_file)
    print(f"PiSignage schedule : {sched.get('playlist') or 'unknown'}")
    if sched.get("last_off_at"):
        print(f"Last TV-off rebuild: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(sched['last_off_at']))}")
    for show in shows:
        state = show.load_state()
        episodes = show.episodes()
        print(f"[{show.name}]")
        print(f"  episodes found : {len(episodes)}")
        for key in ("current", "previous", "first"):
            if state.get(key):
                print(f"  {key:<15}: {os.path.basename(state[key])}")
        for key in ("started", "last_seen", "rebuilt_at"):
            if state.get(key):
                print(f"  {key:<15}: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(state[key]))}")
        if episodes:
            nxt = episodes[resume_index(episodes, state)]
            print(f"  next rebuild   : starts at {os.path.basename(nxt)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["track", "rebuild", "status"])
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args()

    settings, shows = load_config(args.config)
    schedule_file = os.path.join(settings["state_dir"], "schedule.json")
    if args.command == "status":
        status(shows, schedule_file)
        return
    setup_logging(settings)

    lock_path = os.path.join(settings["state_dir"], ".lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "track":
            track(shows)
            log_path = settings.get("pisignage_log", "/home/pi/forever_out.log")
            if scan_schedule(log_path, schedule_file):
                log.info("TV turned off; rebuilding episode folders")
                rebuild(shows)
            warn_if_no_off_events(shows, schedule_file)
        else:
            rebuild(shows)


if __name__ == "__main__":
    main()
