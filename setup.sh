#!/bin/bash
# setup.sh — one-time setup for salesforce-csv-exporter
# Run this once before using any of the scripts.
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh

set -e

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Salesforce CSV Exporter — Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "ERROR: Python 3 is not installed."
    echo "       Download it from https://www.python.org/downloads/ and re-run this script."
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1)
echo "✓  Found $PYTHON_VERSION"

# Install playwright
echo ""
echo "Installing playwright …"
pip3 install -r requirements.txt

# Install the Chromium browser
echo ""
echo "Installing Chromium browser (this may take a minute) …"
playwright install chromium

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Setup complete!"
echo ""
echo "  Run a report or dashboard export with:"
echo "    python3 scrape_sf.py"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
