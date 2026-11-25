#!/usr/bin/env bash
# One-time setup for TestForge on macOS / Linux: bash setup_unix.sh
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -e ".[all]"
python -m playwright install chromium
python -m pytest -q
echo "Setup complete. Start the dashboard with: source .venv/bin/activate && testforge serve"
