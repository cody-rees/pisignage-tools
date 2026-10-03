# pisignage-tools

Small add-ons for a Raspberry Pi running the [PiSignage](https://pisignage.com) player.
Built for a laundromat screen that plays a TV series with ads between episodes.

Not affiliated with PiSignage. Nothing here includes or modifies PiSignage code;
both tools run alongside the player.

Tested on a Raspberry Pi 4, Raspberry Pi OS (Debian 13, 64-bit, labwc/Wayland),
PiSignage player 5.5.4.

## series-rotation

PiSignage plays a "Local Files and folders" asset from the first file every
time the playlist loads: after the TV turns off and on, after a reboot, after a
deploy. With a long series and a daily schedule, the screen replays the same
episodes every day.

`series-rotation` keeps a folder of numbered symlinks whose first entry is
always the next episode to play. Point the PiSignage asset at that folder
instead of the season folders.

- Every minute it records which episode the player has open.
- Whenever PiSignage turns the TV off (it watches `forever_out.log` for
  `**** turning tv OFF ****`), and at boot, it rebuilds the folder starting at
  the episode that was playing. An episode that was cut off replays from the
  start; one that finished moves on.
- After the last episode it wraps around to the first.
- New seasons are picked up at the next rebuild. Season folders are sorted
  naturally (`show_s2` before `show_s10`).
- If the media drive is missing, the existing folder is left untouched.

It only rebuilds while the TV is off because PiSignage keeps its place in a
folder by position, and re-reads the folder every 10 minutes. Reordering the
folder during playback would make it skip episodes.

### Install

```bash
cd series-rotation
nano rotation.conf        # set source_glob and output_dir for each show
sudo ./install.sh         # MOUNT_POINT at the top must match your media drive
```

Then in PiSignage create a "Local Files and folders" asset pointing at
`output_dir` (for example `/home/pi/rotation/happy_days`) and give it a
duration longer than your longest episode. PiSignage stops videos 10 seconds
after the asset's duration, and a duration of 10 means a 5-minute limit.

`output_dir` must be on a filesystem that supports symlinks (not exFAT/FAT).

```bash
./rotation.py status      # what's playing and where the next rebuild starts
./rotation.py rebuild     # rebuild now (only while the TV is off)
python3 -m unittest test_rotation
sudo ./install.sh --uninstall
```

Logs go to `rotation.log` and the systemd journal. If no TV-off is seen for
36 hours while episodes are playing, a warning is logged (for example if a
PiSignage update changes its log wording).

## cursor-autohide

On labwc, PiSignage hides the cursor once at startup. When the display is
switched off and on, labwc shows the cursor again in the middle of the screen.

`cursor-autohide` watches `/dev/input/mice` and, after 5 seconds without
movement, sends the Alt+Super+H keybind that PiSignage's labwc config maps to
`HideCursor`. While idle it repeats this every 5 seconds, so the cursor
disappears shortly after the screen comes back on. Moving a mouse shows it
again immediately.

```bash
cd cursor-autohide
sudo ./install.sh         # IDLE_SECONDS at the top sets the delay
sudo ./install.sh --uninstall
```

Requires `wtype` and a labwc keybind like the one PiSignage installs in
`~/.config/labwc/rc.xml`:

```xml
<keybind key="A-W-h">
  <action name="HideCursor" />
  <action name="WarpCursor" x="-1" y="-1" />
</keybind>
```

## License

MIT, see [LICENSE](LICENSE).
