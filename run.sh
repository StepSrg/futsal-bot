#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source venv/bin/activate
export FUTSAL_BOT_TOKEN="${FUTSAL_BOT_TOKEN:-}"
export FUTSAL_CHAT_ID="${FUTSAL_CHAT_ID:-}"
python bot.py
