#!/usr/bin/env python3
"""
report_export.py — Universal Salesforce Lightning report → CSV exporter.

Intercepts the Aura "runReport" API response the browser fires when it loads
a report — the same technique Ranya's gist uses. No auth headers to steal,
no REST API calls to construct, no CSRF tokens to worry about.
The browser does all the auth work; we just read what it gets back.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USAGE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python3 report_export.py           # prompts for URL interactively
  python3 report_export.py --debug   # also print raw API snippets

WHAT KIND OF URLs WORK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✓ https://ibmsc.lightning.force.com/lightning/r/Report/00OgR000000WWzFUAW/view
  ✓ https://ibmsc.lightning.force.com/lightning/r/Report/00OgR000000WWzFUAW/view?queryScope=userFolders

HOW IT WORKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. Opens a headed Chromium browser on the Salesforce login page.
  2. You log in with IBM w3id — the script detects when login is done.
  3. Navigates to the report URL.
  4. Listens for the Aura "runReport" API response the page fires.
  5. Parses all rows + column headers from that JSON response.
  6. Saves <ReportName>_<timestamp>.csv on your Desktop.

NOTE: This is for standard Lightning Reports only.
      For CRM Analytics / Wave dashboards use wave_export.py instead.
"""

import asyncio
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

DEBUG = "--debug" in sys.argv

try:
    from playwright.async_api import async_playwright, Response
except ImportError:
    print("ERROR: playwright is not installed.")
    print("       Run:  pip install playwright && playwright install chromium")
    sys.exit(1)

SF_LOGIN_URL = "https://ibmsc.my.salesforce.com"


# ── helpers ───────────────────────────────────────────────────────────────────

def safe_filename(s: str) -> str:
    clean = re.sub(r"[^\w\s\-]", "", s).strip()
    clean = re.sub(r"\s+", "_", clean)
    return clean[:80] or "sf_report"


def parse_report_response(body: bytes) -> dict | None:
    """
    Parse a Salesforce Aura runReport JSON response.
    Returns {"title": str, "headers": [...], "rows": [[...], ...]} or None.

    Aura wraps everything in:
      {"actions": [{"returnValue": { <report data> }}]}

    The report data shape:
      returnValue.factMap."T!T".rows[].dataCells  → cell values
      returnValue.reportMetadata.detailColumns     → column API names
      returnValue.reportMetadata.detailColumnInfo  → column labels
    """
    try:
        data = json.loads(body)
    except Exception:
        return None

    if DEBUG:
        if isinstance(data, dict):
            print(f"  [debug] top-level keys: {list(data.keys())}")

    actions = data.get("actions", []) if isinstance(data, dict) else []
    for action in actions:
        rv = action.get("returnValue", {})
        if not isinstance(rv, dict):
            continue

        fact_map = rv.get("factMap", {})
        if not fact_map:
            continue

        # Tabular reports use "T!T"; grouped reports use numeric keys like "0!T"
        if "T!T" in fact_map:
            rows_key = "T!T"
        else:
            rows_key = next(iter(fact_map), None)
        if not rows_key:
            continue

        raw_rows = fact_map[rows_key].get("rows", [])
        if not raw_rows:
            continue

        # Build column headers from metadata
        meta        = rv.get("reportMetadata", {}) or {}
        col_info    = meta.get("detailColumnInfo", {}) or {}
        detail_cols = meta.get("detailColumns", []) or []
        headers = [
            col_info.get(c, {}).get("label", c)
            for c in detail_cols
        ]
        # Fallback: number columns if metadata is missing
        if not headers and raw_rows:
            headers = [f"Column {i+1}" for i in range(len(raw_rows[0].get("dataCells", [])))]

        # Extract row values — use the human-readable "label" field
        rows = []
        for raw in raw_rows:
            cells = raw.get("dataCells", [])
            rows.append([c.get("label", "") if isinstance(c, dict) else str(c) for c in cells])

        title = rv.get("reportName", "") or meta.get("name", "report")

        if DEBUG:
            print(f"  [debug] found {len(rows)} rows, {len(headers)} columns")
            print(f"  [debug] title: {title!r}")

        return {"title": title, "headers": headers, "rows": rows}

    return None


# ── main ──────────────────────────────────────────────────────────────────────

async def run():
    captured: dict = {}   # filled by handle_response when runReport fires

    async def handle_response(response: Response):
        if "runReport" not in response.url:
            return
        try:
            body   = await response.body()
            result = parse_report_response(body)
            if result and result["rows"]:
                # Keep the largest result seen (in case multiple runReport
                # calls fire — e.g. a re-run after filters change)
                if len(result["rows"]) > len(captured.get("rows", [])):
                    captured.update(result)
                    print(f"[*] Captured: {len(result['rows']):,} rows, "
                          f"{len(result['headers'])} columns "
                          f"— \"{result['title']}\"")
        except Exception as e:
            if DEBUG:
                print(f"  [debug] handle_response error: {e}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page    = await context.new_page()
        page.on("response", handle_response)

        # ── get report URL then go directly to it ─────────────────────────────
        print()
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  Salesforce Report Exporter")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print()
        print("[1/2] Paste the report URL from your browser's address bar.")
        print("      Example:")
        print("        https://ibmsc.lightning.force.com/lightning/r/Report/00OgR000000WWzFUAW/view")
        print()
        report_url = (await asyncio.get_event_loop().run_in_executor(
            None, input, "      Report URL > "
        )).strip()

        if not report_url.startswith("http"):
            print("[!] That doesn't look like a valid URL.")
            await browser.close()
            sys.exit(1)

        print()
        print(f"[2/2] Opening report — log in if prompted, then wait for it to load.")
        print(f"      Waiting for report data (up to 60 s) …")
        print()

        await page.goto(report_url, wait_until="domcontentloaded", timeout=120_000)

        # If redirected to SSO login, wait for the user then reload the report
        on_login = False
        try:
            on_login = await page.evaluate(
                "() => !!document.querySelector('input[type=\"password\"]')"
            )
        except Exception:
            pass
        if on_login:
            print("─" * 60)
            print("  LOGIN REQUIRED")
            print("  Log in in the browser window.")
            print("  Once the report is visible, press Enter here.")
            print("─" * 60, end=" ", flush=True)
            await asyncio.get_event_loop().run_in_executor(None, input)
            await page.goto(report_url, wait_until="domcontentloaded", timeout=120_000)
            await page.wait_for_timeout(5_000)

        # Wait up to 60 s for the runReport response to be captured
        for _ in range(120):
            await page.wait_for_timeout(500)
            if captured.get("rows"):
                break
        else:
            print("[!] Timed out — runReport response not captured within 60 s.")
            print("    Make sure:")
            print("      • The URL points to a Lightning Report (not a dashboard)")
            print("      • You are fully logged in")
            print("      • The report loaded completely in the browser")
            print()
            print("    TIP: if the report needs a filter or prompt to run,")
            print("         fill those in the browser window and let it load,")
            print("         then re-run this script.")
            await browser.close()
            sys.exit(1)

        await browser.close()

    # ── write CSV ─────────────────────────────────────────────────────────────
    headers = captured.get("headers", [])
    rows    = captured.get("rows", [])
    title   = captured.get("title", "report")

    if not rows:
        print("[!] No rows collected.")
        sys.exit(1)

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path.home() / "Desktop" / f"{safe_filename(title)}_{ts}.csv"

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)

    print(f"\n[+] Done!")
    print(f"    {len(rows):,} rows  ×  {len(headers)} columns")
    print(f"    Columns: {', '.join(headers)}")
    print(f"    Saved → {out_path}\n")


if __name__ == "__main__":
    asyncio.run(run())
