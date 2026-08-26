#!/usr/bin/env python3
"""
wave_export.py — Universal Salesforce CRM Analytics (Wave) table exporter.

Intercepts the Wave REST API to export ANY table widget from ANY dashboard
to a CSV file — no matter how many rows or columns it has.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USAGE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python3 wave_export.py                     # interactive — prompts you for everything
  python3 wave_export.py --debug             # also print raw API snippets

HOW IT WORKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. Opens a Chromium browser and navigates to the Salesforce login page.
  2. You log in with your IBM w3id credentials.
  3. You paste the dashboard URL (from your browser's address bar) and press Enter.
  4. The script navigates to that URL and listens for ALL Wave table queries.
  5. If the dashboard has more than one table, it lists them and you pick one.
  6. It replays that query with paginated offsets until every row is fetched.
     — Any number of rows, any number of columns — fully automatic.
     — If auth tokens expire mid-run it picks up fresh ones automatically.
  7. Saves <step_id>_<timestamp>.csv on your Desktop.
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
    from playwright.async_api import async_playwright, Request
except ImportError:
    print("ERROR: playwright is not installed.")
    print("       Run:  pip install playwright && playwright install chromium")
    sys.exit(1)

# ── constants ─────────────────────────────────────────────────────────────────

PAGE_SIZE     = 2000   # rows per API request — 2000 is the Wave API maximum
SF_LOGIN_URL  = "https://ibmsc.my.salesforce.com"
WAVE_PATH     = "/analytics/wave/ui?destPath=%2Fservices%2Fdata%2Fv67.0%2Fwave%2Fquery"


# ── helpers ───────────────────────────────────────────────────────────────────

def wave_endpoint(base_url: str) -> str:
    """Build the Wave query endpoint from the Salesforce org base URL."""
    # e.g. https://ibmsc.lightning.force.com  +  /analytics/wave/ui?...
    origin = re.match(r"https://[^/]+", base_url)
    if origin:
        return origin.group(0) + WAVE_PATH
    return base_url.rstrip("/") + WAVE_PATH


def val(v):
    """Flatten a Salesforce label/value dict or plain scalar to a string."""
    if isinstance(v, dict):
        return str(v.get("label", v.get("value", "")))
    return "" if v is None else str(v)


def parse_records(body: bytes):
    """
    Parse a Wave query response.
    Returns (column_keys, list_of_dicts) or (None, None) on failure.
    """
    try:
        data = json.loads(body)
    except Exception:
        return None, None

    if DEBUG:
        top = list(data.keys()) if isinstance(data, dict) else type(data)
        print(f"  [debug] response top-level keys: {top}")

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


def make_template(q: str) -> str:
    """Normalise the SAQL query: replace the concrete offset with a placeholder."""
    tmpl = re.sub(r"q = offset q \d+;", "q = offset q OFFSET;", q)
    if "q = offset q OFFSET;" not in tmpl:
        # query had no offset clause — insert one just before the limit line
        tmpl = re.sub(r"(q = limit q \d+;)", r"q = offset q OFFSET;\n\1", tmpl)
    return tmpl


def safe_filename(s: str) -> str:
    """Strip characters that are invalid in filenames."""
    return re.sub(r'[<>:"/\\|?*]', "_", s)


def prompt(msg: str) -> str:
    """Blocking stdin prompt, safe to call from an async context."""
    return input(msg)


# ── main ──────────────────────────────────────────────────────────────────────

async def run():
    # Dict of step_id → {req_headers, query_tmpl, metadata}
    # Updated live whenever a matching request is intercepted.
    captures      : dict[str, dict] = {}
    refresh_events: dict[str, asyncio.Event] = {}
    endpoint_url  = ""   # filled in once we know the org URL

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
            "query_tmpl":  make_template(q),
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

        # ── get dashboard URL then go directly to it ──────────────────────────
        print()
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  Salesforce Wave Table Exporter")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print()
        print("[1/3] Paste the dashboard URL from your browser's address bar.")
        print("      (It should contain 'lightning/page/analytics' or 'wave')")
        print()
        dashboard_url = await asyncio.get_event_loop().run_in_executor(
            None, prompt, "      Dashboard URL > "
        )
        dashboard_url = dashboard_url.strip()
        if not dashboard_url.startswith("http"):
            print("[!] That doesn't look like a URL. Re-run and paste the full URL.")
            await browser.close()
            sys.exit(1)

        endpoint_url = wave_endpoint(dashboard_url)

        print()
        print("[2/3] Opening dashboard — log in if prompted, then wait for it to load.")
        await page.goto(dashboard_url, wait_until="domcontentloaded", timeout=120_000)

        # If redirected to SSO login, wait then reload
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
            print("  Once the dashboard is visible, press Enter here.")
            print("─" * 60, end=" ", flush=True)
            await asyncio.get_event_loop().run_in_executor(None, prompt)
            await page.goto(dashboard_url, wait_until="domcontentloaded", timeout=120_000)
            await page.wait_for_timeout(5_000)

        print()
        print("[3/3] Waiting up to 90 s for table queries to fire …")
        print("      (scroll the dashboard to make sure all tables are visible)")

        # Wait until at least one table widget query is intercepted
        deadline = asyncio.get_event_loop().time() + 90
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.5)
            if captures:
                break
        else:
            print()
            print("[!] No Wave table queries were intercepted within 90 s.")
            print("    Make sure you are on the right dashboard and the table is visible.")
            await browser.close()
            sys.exit(1)

        # Give it 3 more seconds to pick up any additional tables on the page
        await asyncio.sleep(3)

        # ── step 4: pick the table ────────────────────────────────────────────
        step_ids = list(captures.keys())

        if len(step_ids) == 1:
            chosen = step_ids[0]
            print(f"\n[*] Found 1 table widget: {chosen}")
        else:
            print(f"\n[4/4] Found {len(step_ids)} table widgets on this dashboard:")
            for i, sid in enumerate(step_ids, 1):
                print(f"      [{i}] {sid}")
            print()
            while True:
                raw = await asyncio.get_event_loop().run_in_executor(
                    None, prompt, f"      Which table do you want to export? (1–{len(step_ids)}) > "
                )
                if raw.strip().isdigit() and 1 <= int(raw.strip()) <= len(step_ids):
                    chosen = step_ids[int(raw.strip()) - 1]
                    break
                print(f"      Please enter a number between 1 and {len(step_ids)}.")

        print(f"\n[*] Exporting: {chosen}")

        # ── paginated fetch ───────────────────────────────────────────────────
        print("[*] Starting paginated fetch …\n")

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

            resp = await page.request.fetch(
                endpoint_url,
                method="POST",
                headers=cap["req_headers"],
                data=payload,
            )
            resp_body = await resp.body()

            # ── auth expired — wait for the browser to re-fire the widget ─────
            if resp.status in (401, 403):
                consecutive_errs += 1
                if consecutive_errs > 5:
                    print(f"\n[!] Too many auth errors at offset {offset}. Stopping.")
                    break
                print(f"[!] HTTP {resp.status} — token expired. Waiting for refresh …")
                print("    Scroll the table in the browser to trigger a new query, then wait.")
                refresh_ev.clear()
                # Also update cap pointer in case a fresh capture came in
                try:
                    await asyncio.wait_for(refresh_ev.wait(), timeout=120)
                    cap = captures[chosen]   # pick up the freshest headers
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

            page_keys, page_rows = parse_records(resp_body)

            if not page_rows:
                print(f"[*] No records at offset {offset} — reached end of data.")
                break

            # Merge any newly seen column keys (preserves order, handles variable schemas)
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

    # ── write CSV ─────────────────────────────────────────────────────────────
    if not all_records:
        print("\n[!] No rows were collected. Nothing to save.")
        sys.exit(1)

    print(f"\n[*] Total raw rows fetched: {len(all_records):,}")

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
        seen_rows : set = set()
        deduped = []
        for rec in all_records:
            key = tuple(rec.get(k, "") for k in all_keys)
            if key not in seen_rows:
                seen_rows.add(key)
                deduped.append(rec)
        if len(deduped) < len(all_records):
            print(f"[*] Removed {len(all_records) - len(deduped):,} exact duplicate rows "
                  f"→ {len(deduped):,} rows remaining")
    all_records = deduped

    ts        = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname     = f"{safe_filename(chosen)}_{ts}.csv"
    out_path  = Path.home() / "Desktop" / fname

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(all_keys)           # raw API field names as headers
        for rec in all_records:
            w.writerow([rec.get(k, "") for k in all_keys])

    print(f"\n[+] Done!")
    print(f"    {len(all_records):,} rows  ×  {len(all_keys)} columns")
    print(f"    Columns: {', '.join(all_keys)}")
    print(f"    Saved → {out_path}\n")


if __name__ == "__main__":
    asyncio.run(run())
