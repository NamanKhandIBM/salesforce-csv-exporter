#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║   Salesforce Lightning Report / CRM Analytics Dashboard → CSV    ║
║   Works for ANY report or dashboard — any number of rows/columns ║
╚══════════════════════════════════════════════════════════════════╝

Supports three URL types automatically:
  • Lightning Reports        — intercepts the Aura runReport API response
  • CRM Analytics Dashboards — intercepts Wave REST API query requests
                               and paginates until every row is fetched
  • Lightning List Views     — intercepts the UI API list-records response
                               (/lightning/o/Object/list) and paginates

──────────────────────────────────────────────────────────────────
FIRST-TIME SETUP  (run once)
──────────────────────────────────────────────────────────────────
  pip install playwright
  playwright install chromium

──────────────────────────────────────────────────────────────────
USAGE
──────────────────────────────────────────────────────────────────
  # Report:
  python scrape_sf.py "https://your-org.lightning.force.com/lightning/r/Report/00O.../view"

  # Dashboard:
  python scrape_sf.py "https://your-org.lightning.force.com/lightning/page/analytics?wave__assetType=dashboard&..."

  # Let the script prompt you for the URL:
  python scrape_sf.py

  # Debug mode (prints raw API snippets):
  python scrape_sf.py --debug

──────────────────────────────────────────────────────────────────
HOW IT WORKS — REPORTS
──────────────────────────────────────────────────────────────────
  1. Opens a Chromium browser window on the Salesforce login page.
  2. You log in (IBM w3id SSO supported) — press Enter when done.
  3. Navigates to the report URL.
  4. Intercepts the Aura "runReport" JSON response the page fires —
     which contains every row already formatted.
  5. Parses headers + rows and saves a CSV to your Desktop.

HOW IT WORKS — DASHBOARDS
──────────────────────────────────────────────────────────────────
  1. Opens a Chromium browser window on the URL you provided.
  2. A prompt appears in Terminal: navigate to whatever tab or view
     has the table you want, log in if needed, then press Enter.
  3. The script reloads the page you landed on and intercepts all
     Wave REST API query REQUESTS fired by table widgets.
  4. If there are multiple table widgets, lists them — you pick one.
  5. Replays the intercepted query with paginated offsets (2,000 rows
     at a time) until all rows are fetched.
  6. Deduplicates exact duplicate rows (join-expanded duplicates from
     the Wave API) and saves a CSV to your Desktop.
  7. If auth tokens expire mid-pagination, waits for the browser to
     re-fire the widget and picks up fresh tokens automatically.
"""

import argparse
import asyncio
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ── dependency check ──────────────────────────────────────────────────────────

try:
    from playwright.async_api import async_playwright, Request, Response
except ImportError:
    print("ERROR: Missing required package: playwright")
    print()
    print("Install with:")
    print("  pip install playwright")
    print("  playwright install chromium")
    sys.exit(1)

# ── constants ─────────────────────────────────────────────────────────────────

SF_LOGIN_URL = "https://ibmsc.my.salesforce.com"  # change to your org's login URL
WAVE_PATH    = "/analytics/wave/ui?destPath=%2Fservices%2Fdata%2Fv67.0%2Fwave%2Fquery"
PAGE_SIZE    = 2000  # Wave API maximum rows per request

DEBUG = "--debug" in sys.argv
SNIFF = "--sniff" in sys.argv

# ── helpers ───────────────────────────────────────────────────────────────────

def safe_filename(s: str) -> str:
    clean = re.sub(r"[^\w\s\-]", "", s).strip()
    clean = re.sub(r"\s+", "_", clean)
    return clean[:80] or "sf_export"


def is_dashboard_url(url: str) -> bool:
    """Return True if the URL points to a CRM Analytics dashboard."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    asset_type = qs.get("wave__assetType", [""])[0]
    if asset_type == "dashboard":
        return True
    if "/lightning/page/analytics" in parsed.path and not asset_type:
        return True
    # Standalone Wave dashboard URL (e.g. /analytics/wave/dashboard?assetId=...)
    if "/analytics/wave/dashboard" in parsed.path:
        return True
    return False


def is_listview_url(url: str) -> bool:
    """Return True if the URL is a Lightning List View (/lightning/o/*/list)."""
    return bool(re.search(r"/lightning/o/[^/]+/list\b", urlparse(url).path))


def wave_endpoint(url: str) -> str:
    """Build the Wave query endpoint from the org base URL."""
    origin = re.match(r"https://[^/]+", url)
    return (origin.group(0) if origin else url.rstrip("/")) + WAVE_PATH


# ── report helpers ────────────────────────────────────────────────────────────

def parse_report_response(body: bytes) -> dict | None:
    """
    Parse a Salesforce Aura runReport JSON response.
    Returns {"title": str, "headers": [...], "rows": [[...], ...]} or None.

    Response shape:
      {"actions": [{"returnValue": {
          "reportName": "...",
          "reportMetadata": {
              "detailColumns": ["COL1", ...],
              "detailColumnInfo": {"COL1": {"label": "..."}, ...}
          },
          "factMap": {
              "T!T": {"rows": [{"dataCells": [{"label": "...", "value": "..."}, ...]}, ...]}
          }
      }}]}

    Tabular reports use factMap key "T!T".
    Grouped/matrix reports use numeric keys like "0!T".
    """
    try:
        data = json.loads(body)
    except Exception:
        return None

    if DEBUG:
        if isinstance(data, dict):
            print(f"  [debug] report response keys: {list(data.keys())}")

    actions = data.get("actions", []) if isinstance(data, dict) else []
    for action in actions:
        rv = action.get("returnValue", {})
        if not isinstance(rv, dict):
            continue

        fact_map = rv.get("factMap", {})
        if not fact_map:
            continue

        # Tabular → "T!T", grouped → first numeric key e.g. "0!T"
        rows_key = "T!T" if "T!T" in fact_map else next(iter(fact_map), None)
        if not rows_key:
            continue

        raw_rows = fact_map[rows_key].get("rows", [])
        if not raw_rows:
            continue

        meta        = rv.get("reportMetadata", {}) or {}
        col_info    = meta.get("detailColumnInfo", {}) or {}
        detail_cols = meta.get("detailColumns", []) or []
        headers     = [col_info.get(c, {}).get("label", c) for c in detail_cols]
        # Fallback: number columns when metadata is absent
        if not headers:
            headers = [f"Column {i+1}" for i in range(len(raw_rows[0].get("dataCells", [])))]

        rows = []
        for raw in raw_rows:
            cells = raw.get("dataCells", [])
            rows.append([c.get("label", "") if isinstance(c, dict) else str(c) for c in cells])

        title = rv.get("reportName", "") or meta.get("name", "report")

        if DEBUG:
            print(f"  [debug] report: {len(rows)} rows, {len(headers)} cols, title={title!r}")

        return {"title": title, "headers": headers, "rows": rows}

    return None


# ── dashboard (Wave) helpers ──────────────────────────────────────────────────

def val(v) -> str:
    """Flatten a Wave label/value dict or plain scalar to a string."""
    if isinstance(v, dict):
        return str(v.get("label", v.get("value", "")))
    return "" if v is None else str(v)


def parse_wave_records(body: bytes):
    """
    Parse a Wave query API response.
    Returns (column_keys, list_of_row_dicts) or (None, None) on failure.
    """
    try:
        data = json.loads(body)
    except Exception:
        return None, None

    if DEBUG:
        top = list(data.keys()) if isinstance(data, dict) else type(data)
        print(f"  [debug] wave response keys: {top}")

    results = data.get("results") or {}
    if isinstance(results, str):
        try:
            results = json.loads(results)
        except Exception:
            return None, None

    records = results.get("records")
    if not records:
        if DEBUG:
            print(f"  [debug] results keys: {list(results.keys()) if isinstance(results, dict) else results}")
        return None, None

    keys = list(records[0].keys())
    rows = [{k: val(r.get(k)) for k in keys} for r in records]
    return keys, rows


def make_saql_template(q: str) -> str:
    """Replace the concrete offset value with a placeholder for replay."""
    tmpl = re.sub(r"q = offset q \d+;", "q = offset q OFFSET;", q)
    if "q = offset q OFFSET;" not in tmpl:
        tmpl = re.sub(r"(q = limit q \d+;)", r"q = offset q OFFSET;\n\1", tmpl)
    return tmpl


def saql_summary(q: str) -> str:
    """
    Extract a human-readable summary from a SAQL query string:
      • column names from the first  'q = foreach q generate ...'  clause
      • row limit from 'q = limit q N'

    Returns a short string like '8 cols  ·  limit 2,000' or '' if unparseable.
    """
    parts = []

    # Column names: everything between 'generate' and the next ';'
    gen_m = re.search(r"q\s*=\s*foreach\s+q\s+generate\s+(.+?);", q, re.DOTALL)
    if gen_m:
        raw_cols = gen_m.group(1)
        # Each projection is either  expr as 'alias'  or  expr as alias  or just  expr
        aliases = re.findall(r"as\s+['\"]?(\w+)['\"]?", raw_cols, re.IGNORECASE)
        if aliases:
            col_count = len(aliases)
            col_names = ", ".join(aliases[:4])
            if col_count > 4:
                col_names += f", … (+{col_count - 4} more)"
            parts.append(f"{col_count} cols: {col_names}")
        else:
            # Fallback: count comma-separated top-level projections
            col_count = raw_cols.count(",") + 1
            parts.append(f"~{col_count} cols")

    # Row limit
    lim_m = re.search(r"q\s*=\s*limit\s+q\s+(\d+)", q)
    if lim_m:
        parts.append(f"limit {int(lim_m.group(1)):,}")

    return "  ·  ".join(parts)


# ── login helper ──────────────────────────────────────────────────────────────

async def handle_login(page, url: str) -> str:
    """
    Open the given URL, then pause so the user can:
      • complete SSO login (if not already logged in)
      • navigate to the exact tab / view / screen they want to export

    For dashboards, tabs don't change the URL, so we do NOT reload after the
    user presses Enter — the request listener is already attached and will
    catch the queries that fired when the user clicked the tab.

    Returns the URL of the page that was opened (used for the Wave endpoint).
    """
    print(f"[1] Opening: {url}")
    print()
    await page.goto(url, wait_until="domcontentloaded", timeout=120_000)

    print("─" * 60)
    print("  Navigate to the exact page, tab, or view you want to export.")
    print("  • If you need to log in, do that first.")
    print("  • Then go to the specific tab / screen that has the table.")
    print("  When you can see the data you want, press Enter here.")
    print("─" * 60, end=" ", flush=True)
    await asyncio.get_event_loop().run_in_executor(None, input)

    # Return current URL (for wave_endpoint) but do NOT reload —
    # reloading would reset the tab back to the default (Summary) view.
    print()
    return page.url


# ── report scraper ────────────────────────────────────────────────────────────

async def scrape_report(url: str) -> tuple[list, list[list], str]:
    """
    Open the report URL, intercept the Aura runReport response, and
    return (headers, rows, report_title).
    """
    captured: dict = {}

    async def handle_response(response: Response):
        if "runReport" not in response.url:
            return
        try:
            body   = await response.body()
            result = parse_report_response(body)
            if result and result["rows"]:
                if len(result["rows"]) > len(captured.get("rows", [])):
                    captured.update(result)
                    print(f"[*] Captured: {len(result['rows']):,} rows, "
                          f"{len(result['headers'])} columns — \"{result['title']}\"")
        except Exception as e:
            if DEBUG:
                print(f"  [debug] handle_response error: {e}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page    = await context.new_page()
        page.on("response", handle_response)

        print()
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  Salesforce Report → CSV")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print()
        await handle_login(page, url)

        print()
        print("[*] Waiting for report data to load (up to 60 s) …")
        print(f"    The report must fully load in the browser window.")
        print()

        for _ in range(120):   # 120 × 500 ms = 60 s
            await page.wait_for_timeout(500)
            if captured.get("rows"):
                break
        else:
            print("[!] Timed out — runReport response not captured within 60 s.")
            print("    • Make sure the URL is a Lightning Report (not a dashboard)")
            print("    • Make sure you are fully logged in")
            print("    • If the report has filters/prompts, fill them in the browser")
            await browser.close()
            sys.exit(1)

        await browser.close()

    return captured["headers"], captured["rows"], captured["title"]


# ── dashboard scraper ─────────────────────────────────────────────────────────

async def scrape_dashboard(url: str) -> tuple[list, list[list], str]:
    """
    Open the dashboard URL and pause so the user can navigate to the exact
    tab / view they want.  After they press Enter the page reloads, Wave
    table widget queries are intercepted, paginated, and returned as
    (headers, rows, step_id).

    If multiple table widgets fire queries, the user picks which one to export.
    """
    captures      : dict[str, dict]         = {}
    refresh_events: dict[str, asyncio.Event] = {}

    def get_event(step_id: str) -> asyncio.Event:
        if step_id not in refresh_events:
            refresh_events[step_id] = asyncio.Event()
        return refresh_events[step_id]

    async def on_request(req: Request):
        if WAVE_PATH not in req.url:
            return
        step_id = req.headers.get("uidashboardstepid", "")
        if not step_id:
            return
        try:
            body = json.loads(req.post_data or "{}")
        except Exception:
            return
        q = body.get("query", "")
        if not q:
            return
        captures[step_id] = {
            "req_headers": dict(req.headers),
            "query_tmpl":  make_saql_template(q),
            "metadata":    body.get("metadata", {}),
        }
        get_event(step_id).set()
        if DEBUG:
            print(f"  [debug] intercepted step: {step_id}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page    = await context.new_page()
        page.on("request", on_request)

        print()
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  Salesforce Dashboard → CSV")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print()
        final_url = await handle_login(page, url)

        print()
        print("[*] Waiting up to 90 s for table widget queries to fire …")
        print("    (scroll the table into view if it is not already visible)")
        deadline = asyncio.get_event_loop().time() + 90
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.5)
            if captures:
                break
        else:
            print("[!] No Wave table queries intercepted within 90 s.")
            print("    Make sure you are on the correct tab and the table is visible.")
            await browser.close()
            sys.exit(1)

        await asyncio.sleep(3)   # collect any additional widget queries

        # Pick which table to export
        step_ids = list(captures.keys())
        if len(step_ids) == 1:
            chosen = step_ids[0]
            summary = saql_summary(captures[chosen]["query_tmpl"])
            print(f"[*] Found 1 table widget: {chosen}" + (f"  ({summary})" if summary else ""))
        else:
            print(f"\n[*] Found {len(step_ids)} table widgets on this dashboard:")
            for i, sid in enumerate(step_ids, 1):
                summary = saql_summary(captures[sid]["query_tmpl"])
                suffix  = f"  ({summary})" if summary else ""
                print(f"    [{i}] {sid}{suffix}")
            print()
            while True:
                raw = await asyncio.get_event_loop().run_in_executor(
                    None, input, f"    Which table to export? (1–{len(step_ids)}) > "
                )
                if raw.strip().isdigit() and 1 <= int(raw.strip()) <= len(step_ids):
                    chosen = step_ids[int(raw.strip()) - 1]
                    break
                print(f"    Enter a number between 1 and {len(step_ids)}.")

        print(f"\n[*] Exporting: {chosen}")
        print("[*] Starting paginated fetch …\n")

        endpoint_url     = wave_endpoint(final_url)
        cap              = captures[chosen]
        refresh_ev       = get_event(chosen)
        all_records      : list[dict] = []
        all_keys         : list[str]  = []
        seen_keys        : set[str]   = set()
        offset           = 0
        consecutive_errs = 0

        while True:
            query = cap["query_tmpl"].replace(
                "q = offset q OFFSET;", f"q = offset q {offset};"
            )
            query = re.sub(r"q = limit q \d+;", f"q = limit q {PAGE_SIZE};", query)

            payload = json.dumps({
                "query":    query,
                "metadata": {**cap["metadata"], "queryId": offset},
            })

            # Wrap fetch in retry loop — ECONNRESET happens on long pagination
            # runs when the server drops the connection; a short wait + retry fixes it.
            resp = None
            for attempt in range(4):
                try:
                    resp = await page.request.fetch(
                        endpoint_url, method="POST",
                        headers=cap["req_headers"], data=payload,
                    )
                    break  # success
                except Exception as e:
                    if "ECONNRESET" in str(e) or "ECONNREFUSED" in str(e) or "socket" in str(e).lower():
                        wait = 3 * (attempt + 1)
                        print(f"[!] Connection reset at offset {offset} (attempt {attempt+1}/4) — retrying in {wait}s …")
                        await asyncio.sleep(wait)
                    else:
                        raise  # unexpected error — let it bubble up
            if resp is None:
                print(f"[!] Could not connect after 4 attempts at offset {offset}. Stopping.")
                break

            resp_body = await resp.body()

            # Auth expired — wait for browser to re-fire the widget
            if resp.status in (401, 403):
                consecutive_errs += 1
                if consecutive_errs > 5:
                    print(f"[!] Too many auth errors at offset {offset}. Stopping.")
                    break
                print(f"[!] HTTP {resp.status} — token expired. Waiting for refresh …")
                print("    Scroll the table in the browser to trigger a new query.")
                refresh_ev.clear()
                try:
                    await asyncio.wait_for(refresh_ev.wait(), timeout=120)
                    cap = captures[chosen]
                except asyncio.TimeoutError:
                    print("[!] Token refresh timed out. Stopping.")
                    break
                continue

            if not resp.ok:
                print(f"[!] HTTP {resp.status} at offset {offset}")
                if DEBUG:
                    print("  ", resp_body[:400].decode("utf-8", errors="replace"))
                consecutive_errs += 1
                if consecutive_errs > 3:
                    print("[!] Too many consecutive errors. Stopping.")
                    break
                continue

            consecutive_errs = 0

            if DEBUG:
                print(f"  [debug] offset {offset}:", resp_body[:200].decode("utf-8", errors="replace"))

            page_keys, page_rows = parse_wave_records(resp_body)

            if not page_rows:
                print(f"[*] No records at offset {offset} — end of data.")
                break

            for k in page_keys:
                if k not in seen_keys:
                    all_keys.append(k)
                    seen_keys.add(k)

            all_records.extend(page_rows)
            print(f"    {len(all_records):,} rows fetched …")

            if len(page_rows) < PAGE_SIZE:
                print("[*] Last page received.")
                break
            offset += PAGE_SIZE

        await browser.close()

    if not all_records:
        print("[!] No rows collected.")
        sys.exit(1)

    print(f"\n[*] Total raw rows: {len(all_records):,}")

    # Deduplicate by AccountNumber — the Wave API returns one account joined to
    # multiple RDC customer/industry records, so the same AccountNumber appears
    # many times with different RDC.CUST_NAME / RDC.INDUSTRY_NAME values.
    # Keeping the first occurrence per AccountNumber matches what the dashboard
    # displays (one row per account).
    # Fall back to full-row dedup if AccountNumber is not in the result set.
    acct_key = "AccountNumber"
    if acct_key in seen_keys:
        seen_accts : set = set()
        deduped = []
        for rec in all_records:
            a = rec.get(acct_key, "")
            if a not in seen_accts:
                seen_accts.add(a)
                deduped.append(rec)
        print(f"[*] Deduplicated by AccountNumber: "
              f"{len(all_records):,} raw rows → {len(deduped):,} unique accounts")
    else:
        # No AccountNumber column — fall back to full-row exact dedup
        seen_rows : set = set()
        deduped = []
        for rec in all_records:
            key = tuple(rec.get(k, "") for k in all_keys)
            if key not in seen_rows:
                seen_rows.add(key)
                deduped.append(rec)
        if len(deduped) < len(all_records):
            print(f"[*] Removed {len(all_records) - len(deduped):,} exact duplicate rows "
                  f"→ {len(deduped):,} remaining")

    rows    = [[rec.get(k, "") for k in all_keys] for rec in deduped]
    headers = all_keys
    return headers, rows, chosen



# ── list view scraper ─────────────────────────────────────────────────────────

async def scrape_listview(url: str) -> tuple[list, list[list], str]:
    """
    Open a Lightning List View URL (/lightning/o/Object/list), intercept the
    UI API list-records response, paginate through all pages, and return
    (headers, rows, title).

    The UI API paginates via pageToken rather than numeric offsets.
    Each response contains nextPageToken (null when done).
    """
    # Extract object name and list name from URL for the output title
    path_m  = re.search(r"/lightning/o/([^/]+)/list", urlparse(url).path)
    obj_name = path_m.group(1) if path_m else "ListExport"
    qs       = parse_qs(urlparse(url).query)
    filter_name = qs.get("filterName", [obj_name])[0]
    title    = f"{obj_name}_{filter_name}"

    captured: dict = {}   # stores first intercepted response details

    async def on_response(resp: Response):
        if "ui-api/list-records" not in resp.url:
            return
        if resp.status != 200:
            return
        if captured:          # already have what we need
            return
        try:
            body = await resp.body()
            data = json.loads(body)
        except Exception:
            return
        records_block = data.get("records", {})
        if not isinstance(records_block, dict):
            return
        raw_records = records_block.get("records", [])
        if not raw_records:
            return
        captured["base_url"]       = resp.url          # includes fields= params
        captured["req_headers"]    = {}                # cookies handled by page.request.fetch
        captured["next_token"]     = records_block.get("nextPageToken")
        captured["first_records"]  = raw_records
        if DEBUG:
            print(f"  [debug] list-records intercepted: {resp.url[:120]}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page    = await context.new_page()
        page.on("response", on_response)

        print()
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  Salesforce List View → CSV")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print()
        await handle_login(page, url)

        print()
        print("[*] Waiting up to 60 s for list data to load …")
        print("    (make sure the list is visible in the browser window)")
        for _ in range(120):        # 120 × 500 ms = 60 s
            await page.wait_for_timeout(500)
            if captured:
                break
        else:
            print("[!] Timed out — no ui-api/list-records response captured.")
            print("    • Make sure the URL is a Lightning List View")
            print("      (address bar contains /lightning/o/.../list)")
            print("    • Scroll the list into view and try again")
            await browser.close()
            sys.exit(1)

        # ── paginate ──────────────────────────────────────────────────────────
        def parse_records(raw_records: list) -> tuple[list[str], list[dict]]:
            """Extract column keys and flat row dicts from UI API records."""
            if not raw_records:
                return [], []
            keys = list(raw_records[0].get("fields", {}).keys())
            rows = []
            for rec in raw_records:
                fields = rec.get("fields", {})
                row = {}
                for k in keys:
                    f = fields.get(k, {})
                    dv = f.get("displayValue")
                    row[k] = str(dv) if dv is not None else (
                        "" if f.get("value") is None else str(f.get("value"))
                    )
                rows.append(row)
            return keys, rows

        all_keys    : list[str]  = []
        seen_keys   : set[str]   = set()
        all_records : list[dict] = []

        # Process the first page that was already intercepted
        first_keys, first_rows = parse_records(captured["first_records"])
        for k in first_keys:
            if k not in seen_keys:
                all_keys.append(k)
                seen_keys.add(k)
        all_records.extend(first_rows)
        print(f"    {len(all_records):,} rows fetched …")

        # Strip any existing pageToken from the base URL so we can append cleanly
        base_url     = re.sub(r"[?&]pageToken=[^&]*", "", captured["base_url"])
        sep          = "&" if "?" in base_url else "?"
        next_token   = captured["next_token"]

        while next_token:
            fetch_url = f"{base_url}{sep}pageToken={next_token}"
            try:
                resp = await page.request.fetch(fetch_url, method="GET")
            except Exception as e:
                print(f"[!] Request error: {e} — stopping.")
                break

            if not resp.ok:
                print(f"[!] HTTP {resp.status} — stopping pagination.")
                break

            try:
                data = json.loads(await resp.body())
            except Exception:
                break

            records_block = data.get("records", {})
            raw_records   = records_block.get("records", [])
            if not raw_records:
                break

            page_keys, page_rows = parse_records(raw_records)
            for k in page_keys:
                if k not in seen_keys:
                    all_keys.append(k)
                    seen_keys.add(k)
            all_records.extend(page_rows)
            print(f"    {len(all_records):,} rows fetched …")

            next_token = records_block.get("nextPageToken")

        print("[*] Last page received.")
        await browser.close()

    if not all_records:
        print("[!] No rows collected.")
        sys.exit(1)

    rows    = [[rec.get(k, "") for k in all_keys] for rec in all_records]
    headers = all_keys
    print(f"\n[*] Total: {len(rows):,} rows, {len(headers)} columns")
    return headers, rows, title




# ── sniffer (diagnosis tool) ──────────────────────────────────────────────────

async def sniff_requests(url: str) -> None:
    """
    Open the page, wait for the user to load whatever they want,
    then print every unique XHR/fetch API URL seen — grouped by path prefix.
    Use this to discover which API endpoint a page type uses.
    """
    seen: dict[str, int] = {}   # path → count

    def on_request(req: Request):
        if req.resource_type not in ("xhr", "fetch"):
            return
        # Strip query string for grouping, keep it for display
        parsed = urlparse(req.url)
        key = parsed.scheme + "://" + parsed.netloc + parsed.path
        seen[req.url] = seen.get(req.url, 0) + 1

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page    = await context.new_page()
        page.on("request", on_request)

        print()
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  Sniff mode — API request inspector")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        await handle_login(page, url)

        print()
        print("[*] Scroll and interact with the page so all requests fire.")
        print("    Press Enter again when ready to see the results.")
        print()
        await asyncio.get_event_loop().run_in_executor(None, input)

        await browser.close()

    print()
    print("─" * 70)
    print("  API requests captured (XHR/fetch only):")
    print("─" * 70)
    # Sort by path so related endpoints group together
    for full_url in sorted(seen.keys()):
        parsed = urlparse(full_url)
        path_and_query = parsed.path + ("?" + parsed.query[:80] if parsed.query else "")
        print(f"  {path_and_query}")
    print("─" * 70)
    print(f"  Total unique requests: {len(seen)}")
    print()
    print("  Paste the output above into the chat so the right endpoint")
    print("  can be added to the scraper.")
    print()



# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Export a Salesforce Lightning report or CRM Analytics dashboard to CSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("url", nargs="?", help="Full Salesforce URL (report or dashboard)")
    parser.add_argument("-o", "--output",
                        help="Output CSV path (default: auto-named file on your Desktop)")
    parser.add_argument("--debug", action="store_true", help="Print raw API snippets")
    parser.add_argument("--sniff", action="store_true",
                        help="Open the page and print all API requests fired — use to diagnose unrecognised pages")
    args = parser.parse_args()

    url = args.url
    if not url:
        url = input("Paste the Salesforce URL and press Enter:\n> ").strip()
    if not url:
        print("No URL provided. Exiting.")
        sys.exit(1)

    if SNIFF:
        asyncio.run(sniff_requests(url))
        sys.exit(0)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if is_listview_url(url):
        headers, rows, title = asyncio.run(scrape_listview(url))
    elif is_dashboard_url(url):
        headers, rows, title = asyncio.run(scrape_dashboard(url))
    else:
        headers, rows, title = asyncio.run(scrape_report(url))

    if args.output:
        out_path = Path(args.output).expanduser()
    else:
        out_path = Path.home() / "Desktop" / f"{safe_filename(title)}_{ts}.csv"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    print(f"\n✓  {len(rows):,} rows  ×  {len(headers)} columns  →  {out_path}\n")
    print(f"   Columns: {', '.join(headers)}\n")


if __name__ == "__main__":
    main()
