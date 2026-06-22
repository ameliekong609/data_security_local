#!/bin/zsh
set -e

cd "$(dirname "$0")"

echo "Starting Data Security Local..."

PYTHON_CMD=""
if command -v python3.11 >/dev/null 2>&1; then
  PYTHON_CMD="$(command -v python3.11)"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON_CMD="$(command -v python3.12)"
fi

if [ -z "$PYTHON_CMD" ]; then
  echo "Python 3.11 or 3.12 is required. Presidio does not support Python 3.14 yet."
  read -r "?Press Enter to close."
  exit 1
fi

if [ ! -d ".venv_local" ]; then
  echo "Creating local Python environment with $($PYTHON_CMD --version)..."
  "$PYTHON_CMD" -m venv .venv_local
fi

source .venv_local/bin/activate

if ! python -m pip --version >/dev/null 2>&1; then
  echo "Repairing local Python environment because pip is missing..."
  deactivate 2>/dev/null || true
  rm -rf .venv_local
  "$PYTHON_CMD" -m venv .venv_local
  source .venv_local/bin/activate
  python -m ensurepip --upgrade
fi

echo "Installing/updating app dependencies..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if ! command -v tesseract >/dev/null 2>&1; then
  echo ""
  echo "Note: Tesseract OCR is not installed."
  echo "PDF text redaction can still run, but scanned images/PDF OCR may not work."
  echo "On Mac, install it with: brew install tesseract"
  echo ""
fi

echo "Opening local desktop app"
python desktop_app.py
