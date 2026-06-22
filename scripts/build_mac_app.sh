#!/bin/zsh
set -e

cd "$(dirname "$0")/.."

PYTHON_CMD=""
if command -v python3.11 >/dev/null 2>&1; then
  PYTHON_CMD="$(command -v python3.11)"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON_CMD="$(command -v python3.12)"
else
  echo "Python 3.11 or 3.12 is required to build the app."
  exit 1
fi

if [ ! -d ".venv_build" ]; then
  echo "Creating build environment with $($PYTHON_CMD --version)..."
  "$PYTHON_CMD" -m venv .venv_build
fi

source .venv_build/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt

rm -rf build dist
python -m PyInstaller --clean --noconfirm packaging/DataSecurityLocal.spec

APP_PATH="dist/Data Security Local.app"
if [ -d "$APP_PATH" ]; then
  xattr -cr "$APP_PATH" 2>/dev/null || true
  codesign --force --deep --sign - "$APP_PATH" 2>/dev/null || true
fi

echo ""
echo "Built:"
echo "  $APP_PATH"
echo ""
echo "For sharing, zip the app:"
echo "  ditto -c -k --sequesterRsrc --keepParent 'dist/Data Security Local.app' 'dist/Data Security Local-mac.zip'"
