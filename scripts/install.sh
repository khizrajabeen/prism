#!/usr/bin/env bash
# NeoBrain installer. Idempotent — safe to re-run.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

echo "NeoBrain install → $ROOT"

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found. Install Python 3.10+ and re-run." >&2
    exit 1
fi

PYV=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  python $PYV"

if [ ! -d .venv ]; then
    python3 -m venv .venv
    echo "  created .venv"
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pip install --quiet --upgrade pip
pip install --quiet -e .
echo "  installed neobrain (core deps only)"

neobrain init
echo
neobrain doctor
echo
echo "Next:"
echo "  source .venv/bin/activate"
echo "  neobrain sweep --days 30"
echo "  neobrain brief"
echo
echo "Optional extras when you want them:"
echo "  pip install -e '.[embeddings]'   # local vectors"
echo "  pip install -e '.[pdf]'          # ingest your own PDFs"
echo "  pip install -e '.[mcp]'          # agent tool server"
