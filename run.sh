#!/usr/bin/env bash
# One-command start for macOS / Linux:  ./run.sh   (add --mock to try the sample data)
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
if ! $PY -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  echo "Python 3.9+ is required (3.11+ recommended). On macOS: brew install python@3.12" >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Creating virtual environment…"
  $PY -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip >/dev/null
pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env. Add your SERPAPI_API_KEY (and optionally GEMINI_API_KEY) to it."
fi

if [ "${1:-}" = "--mock" ]; then
  export PANCHAYAT_MOCK=1
fi

exec python -m backend
