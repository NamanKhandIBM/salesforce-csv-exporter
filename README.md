# Salesforce → CSV Exporter

Export **any** Salesforce Lightning report or CRM Analytics dashboard table to a CSV file on your Desktop — no IT access, no permissions needed, no copy-pasting.

There are **two scripts**, one for each type of page:

| Script | What it exports |
|---|---|
| `scrape_sf.py` | Lightning Reports **and** CRM Analytics Dashboards (auto-detects) |
| `report_export.py` | Lightning Reports only |
| `wave_export.py` | CRM Analytics Dashboards only |

> **Not sure which to use?**  
> Just use `scrape_sf.py` — it figures it out from the URL you paste.

---

## First-time setup (do this once)

### 1. Check if Python is installed

Open **Terminal** and type:

```bash
python3 --version
```

If you see something like `Python 3.11.0` you are good. If you get an error, download Python from [python.org](https://www.python.org/downloads/) and install it.

### 2. Clone the repo

```bash
git clone https://github.com/NamanKhandIBM/salesforce-csv-exporter.git
cd salesforce-csv-exporter
```

### 3. Run the setup script (installs everything automatically)

```bash
./setup.sh
```

This installs `playwright` and downloads the Chromium browser. Takes about a minute. **Only needs to be done once.**

> **Windows users:** run these two commands instead of `./setup.sh`:
> ```
> pip3 install playwright
> playwright install chromium
> ```

---

## How to run it

Every time you want to export, open Terminal and run:

```bash
cd salesforce-csv-exporter
python3 scrape_sf.py
```

Then follow the prompts:

**Step 1 — Log in**

A browser window opens and goes directly to the Salesforce page. If you are not logged in, complete your IBM w3id login. Once you can see the report or dashboard, press **Enter** in Terminal.

**Step 2 — Paste the URL**

Go to the report or dashboard you want to export in the browser, copy the full URL from the address bar, and paste it when prompted:

```
Paste the Salesforce URL and press Enter:
> https://ibmsc.lightning.force.com/lightning/r/Report/00OgR000000WWzFUAW/view
```

**Step 3 — Wait**

- **Reports:** the browser loads the report and captures the data automatically. Takes a few seconds.
- **Dashboards:** the script paginates through all rows — you will see a counter like `2,000 rows fetched … 4,000 rows fetched …`. Large tables (50,000+ rows) may take a few minutes.

If the dashboard has **multiple tabs** (e.g. Summary, Accounts, Products), the script detects them automatically and asks which one you want:

```
[*] This dashboard has 4 tabs:
    [1] Summary
    [2] Accounts
    [3] Products
    [4] Search for Sellers

    Which tab has the table you want to export?
    Tab number (1–4, or press Enter to stay on current tab) > 2
```

Type the number and press Enter — the script clicks the tab and waits for the table to load before exporting.

If the dashboard has **multiple tables on the same tab**, the script lists them and asks you to type the number of the one you want.

**Step 4 — Find your CSV**

When done you will see something like:

```
✓  88,798 rows × 10 columns → /Users/yourname/Desktop/My_Report_20260825_143022.csv
```

The file is on your **Desktop**, named after the report or table with a timestamp.

---

## Passing the URL directly (optional shortcut)

Instead of being prompted, you can pass the URL straight in the command:

```bash
python3 scrape_sf.py "https://ibmsc.lightning.force.com/lightning/r/Report/00OgR000000WWzFUAW/view"
```

---

## What URL do I paste?

Copy the full URL from your browser's address bar while on the page you want to export.

**Lightning Report** — URL contains `/lightning/r/Report/`:
```
https://ibmsc.lightning.force.com/lightning/r/Report/00OgR000000WWzFUAW/view
```

**CRM Analytics Dashboard** — URL contains `wave__assetType=dashboard`:
```
https://ibmsc.lightning.force.com/lightning/page/analytics?wave__assetType=dashboard&wave__assetId=0FK...
```

---

## Getting updates

When a new version is released, pull the latest changes:

```bash
cd salesforce-csv-exporter
git pull
```

---

## Troubleshooting

**`zsh: command not found: python`**  
Use `python3` instead of `python` — macOS does not have a `python` command by default.

**`zsh: no matches found: scrape_sf (1).py`**  
You have a duplicate download with `(1)` in the filename. Run from the cloned repo folder instead:
```bash
cd salesforce-csv-exporter
python3 scrape_sf.py
```

**"No rows captured" / script times out**  
The report or table did not finish loading. Make sure:
- The report fully loaded in the browser (you can see the data)
- You are still logged in
- If the report has a filter or date prompt, fill it in the browser first, then re-run

**"runReport response not captured" for a report**  
The URL must be a standard Lightning Report (address bar contains `/lightning/r/Report/`). CRM Analytics dashboards use a different URL — the script handles both automatically.

**Dashboard table not found / 90-second timeout**  
The table widget may not have loaded yet. Scroll the dashboard so the table is visible in the browser window and re-run.

**Connection reset mid-export (`ECONNRESET`)**  
The script automatically retries up to 4 times with a short wait. You will see:
```
[!] Connection reset at offset 80000 (attempt 1/4) — retrying in 3s …
```
Just let it run — it will recover on its own.

**Token expired mid-export (HTTP 401/403)**  
For large dashboard exports, session tokens can expire. The script will print:
```
[!] HTTP 401 — token expired. Waiting for refresh …
    Scroll the table in the browser to trigger a new query.
```
Scroll the table in the browser — this triggers a fresh request and the export continues automatically.

**`playwright is not installed` error**  
Run the setup again:
```bash
./setup.sh
```

---

## Notes

- The CSV is saved to your **Desktop** automatically. Change the save location with `-o`:
  ```bash
  python3 scrape_sf.py "https://..." -o ~/Documents/my_export.csv
  ```
- **No credentials are ever stored** — the script uses your existing browser session.
- The script opens a real browser window. You always log in yourself — nothing is automated through the login.
- For dashboards, the Wave API sometimes returns the same account linked to multiple territories. The script automatically deduplicates by Account Number so the row count matches what the dashboard shows.
