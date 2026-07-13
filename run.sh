#!/bin/bash
# NYC Property Violations Scanner — Mac Launcher
# Double-click this file or run: bash run.sh

# Always run from the folder this script lives in
cd "$(dirname "$0")"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  NYC Property Violations Scanner"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Check for Python 3 ────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "  ERROR: Python 3 is not installed."
    echo ""
    echo "  Install it one of these ways:"
    echo "    • Download from  https://www.python.org/downloads/"
    echo "    • Homebrew:      brew install python3"
    echo ""
    read -p "  Press Enter to exit..."
    exit 1
fi

PYTHON=$(command -v python3)
echo "  Python: $($PYTHON --version)"
echo ""

# ── Install / update dependencies ────────────────────────────────
echo "  Installing required packages (fast if already installed)..."
$PYTHON -m pip install --quiet -r requirements.txt

echo "  Installing Chromium browser for CityPay scanner..."
$PYTHON -m playwright install chromium --quiet

echo ""
echo "  Setup complete. Launching scanner..."
echo ""

# ── Launch interactive menu ───────────────────────────────────────
$PYTHON run.py
