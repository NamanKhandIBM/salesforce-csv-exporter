#!/bin/bash
# Double-click this file on Mac to run the Salesforce CSV exporter.
# It opens a Terminal window automatically.

cd "$(dirname "$0")"
python3 scrape_sf.py
