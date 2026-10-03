#!/usr/bin/env python3
"""Hide the mouse cursor after a few seconds without pointer movement.

labwc already shows a hidden cursor again as soon as the pointer moves. This
watches /dev/input/mice (all mice, including ones plugged in later) and, once
the pointer has been idle for IDLE_SECONDS, sends the Alt+Super+H keybind that
~/.config/labwc/rc.xml maps to HideCursor. While idle it re-sends the keybind
every REHIDE_SECONDS, because labwc shows the cursor again when the display is
switched off and on without any pointer movement.
"""

import os
import select
import subprocess
import sys
import time

MICE = "/dev/input/mice"
IDLE_SECONDS = float(os.environ.get("IDLE_SECONDS", "5"))
REHIDE_SECONDS = float(os.environ.get("REHIDE_SECONDS", "5"))
HIDE_COMMAND = ["wtype", "-M", "alt", "-M", "logo", "h", "-m", "alt", "-m", "logo"]


def log(message):
    print(message, flush=True)


def hide_cursor():
    """Returns None on success, otherwise the error text."""
    try:
        result = subprocess.run(HIDE_COMMAND, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    if result.returncode != 0:
        return f"exit {result.returncode}: {result.stderr.strip()}"
    return None


def main():
    try:
        mice = os.open(MICE, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        log(f"cannot open {MICE}: {e}")
        sys.exit(1)

    last_motion = time.monotonic() - IDLE_SECONDS
    last_hide = 0.0
    hide_failing = False
    log(f"watching {MICE}; hiding after {IDLE_SECONDS:g}s idle, re-hiding every {REHIDE_SECONDS:g}s")
    while True:
        ready, _, _ = select.select([mice], [], [], 1.0)
        if ready:
            try:
                if os.read(mice, 4096):
                    last_motion = time.monotonic()
            except BlockingIOError:
                pass
        now = time.monotonic()
        if now - last_motion >= IDLE_SECONDS and now - last_hide >= REHIDE_SECONDS:
            error = hide_cursor()
            if error and not hide_failing:
                log(f"hide failed (will keep retrying quietly): {error}")
            elif not error and hide_failing:
                log("hide working again")
            hide_failing = bool(error)
            last_hide = now


if __name__ == "__main__":
    main()
