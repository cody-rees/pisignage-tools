#!/bin/bash
# Installs (or with --uninstall, removes) the cursor auto-hide service.
# Usage: sudo ./install.sh [--uninstall]
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="pi"
RUN_UID="$(id -u "$RUN_USER")"
IDLE_SECONDS=5
UNIT="/etc/systemd/system/cursor-autohide.service"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo" >&2
    exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
    systemctl disable --now cursor-autohide.service 2>/dev/null || true
    rm -f "$UNIT"
    systemctl daemon-reload
    echo "Removed."
    exit 0
fi

chmod +x "$DIR/cursor_autohide.py"
cat > "$UNIT" <<EOF
[Unit]
Description=Hide the mouse cursor after ${IDLE_SECONDS}s without movement
After=graphical.target

[Service]
Type=simple
User=$RUN_USER
Environment=WAYLAND_DISPLAY=wayland-0
Environment=XDG_RUNTIME_DIR=/run/user/$RUN_UID
Environment=IDLE_SECONDS=$IDLE_SECONDS
ExecStart=/usr/bin/python3 $DIR/cursor_autohide.py
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target
EOF

systemctl daemon-reload
systemctl enable --now cursor-autohide.service
systemctl --no-pager --lines=3 status cursor-autohide.service
