#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/tg-activity-sender"

cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
mkdir -p data/media sessions logs
cp deploy/tg-activity-sender.service /etc/systemd/system/tg-activity-sender.service
systemctl daemon-reload
systemctl enable tg-activity-sender
systemctl restart tg-activity-sender
systemctl status tg-activity-sender --no-pager

