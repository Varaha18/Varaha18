#!/bin/sh
# Daily unattended run for cron. Cron doesn't load your shell profile, so the
# API key and mail password are read from .env in this folder (git-ignored).
cd "$(dirname "$0")" || exit 1
. ./.env
mkdir -p data
.venv/bin/python -m jobbot run --mode auto >> data/cron.log 2>&1
