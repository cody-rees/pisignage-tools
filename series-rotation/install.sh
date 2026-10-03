#!/bin/bash
# Installs (or with --uninstall, removes) the series rotation systemd units.
# Usage: sudo ./install.sh [--uninstall]
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="pi"
MOUNT_POINT="/media/pi/ssd"
UNIT_DIR="/etc/systemd/system"
UNITS=(series-rotation-track.service series-rotation-track.timer
       series-rotation-boot.service)
# Units from earlier versions, removed on install and uninstall.
OLD_UNITS=(series-rotation-nightly.service series-rotation-nightly.timer)

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo" >&2
    exit 1
fi

systemctl disable --now series-rotation-nightly.timer 2>/dev/null || true
for unit in "${OLD_UNITS[@]}"; do rm -f "$UNIT_DIR/$unit"; done

if [ "${1:-}" = "--uninstall" ]; then
    systemctl disable --now series-rotation-track.timer series-rotation-boot.service 2>/dev/null || true
    for unit in "${UNITS[@]}"; do rm -f "$UNIT_DIR/$unit"; done
    systemctl daemon-reload
    echo "Removed. The folder and state in $DIR and the rotation folder were left in place."
    exit 0
fi

MOUNT_UNIT="$(systemd-escape -p --suffix=mount "$MOUNT_POINT")"
chmod +x "$DIR/rotation.py"
mkdir -p "$DIR/state"
chown -R "$RUN_USER:$RUN_USER" "$DIR"

cat > "$UNIT_DIR/series-rotation-track.service" <<EOF
[Unit]
Description=Series rotation: record which episode PiSignage is playing

[Service]
Type=oneshot
User=$RUN_USER
ExecStart=/usr/bin/python3 $DIR/rotation.py track
EOF

cat > "$UNIT_DIR/series-rotation-track.timer" <<EOF
[Unit]
Description=Series rotation: record the playing episode every minute

[Timer]
OnCalendar=minutely
AccuracySec=1s

[Install]
WantedBy=timers.target
EOF

cat > "$UNIT_DIR/series-rotation-boot.service" <<EOF
[Unit]
Description=Series rotation: rebuild episode folder before PiSignage starts
After=local-fs.target $MOUNT_UNIT
Before=display-manager.service graphical.target

[Service]
Type=oneshot
User=$RUN_USER
ExecStart=/usr/bin/python3 $DIR/rotation.py rebuild
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable series-rotation-boot.service
systemctl enable --now series-rotation-track.timer
echo "Installed. Tracking every minute; rebuilding whenever PiSignage turns the TV off and at boot."
systemctl list-timers 'series-rotation-*' --no-pager
