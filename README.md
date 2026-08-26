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

You need Python and two small packages installed. This takes about 2 minutes.

### 1. Check if Python is installed

Open **Terminal** (Mac) or **Command Prompt** (Windows) and type:

```
python3 --version
```

If you see something like `Python 3.11.0` you are good. If you get an error, download Python from [python.org](https://www.python.org/downloads/) and install it.

### 2. Install the required packages

Copy and paste these two commands into Terminal, pressing Enter after each:

```
pip install playwright
playwright install chromium
```

The second command downloads a small browser — it may take a minute or two. You only need to do this once.

### 3. Download the script

Save `scrape_sf.py` to a folder on your computer (e.g. your Desktop or Documents).

---

## How to run it

### Step 1 — Open Terminal in the right folder

**Mac:**  
Open Terminal, then type `cd ` (with a space), then drag the folder where you saved the script into the Terminal window, and press Enter.

**Windows:**  
Open the folder where you saved the script, hold Shift and right-click on an empty area, and choose **"Open PowerShell window here"**.

### Step 2 — Run the script

```
python3 scrape_sf.py
```

A browser window will open automatically.

### Step 3 — Log in

Log in to Salesforce in the browser window using your IBM w3id credentials (the same way you normally log in). Complete any MFA steps.

Once you can see a Salesforce page, come back to Terminal and **press Enter**.

### Step 4 — Paste the URL

Go to the report or dashboard you want to export in the browser, copy the URL from the address bar, paste it into Terminal when prompted, and press Enter.

```
Paste the Salesforce URL and press Enter:
> https://ibmsc.lightning.force.com/lightning/r/Report/00OgR000000WWzFUAW/view
```

### Step 5 — Wait for it to finish

For **reports**: the browser will load the report and the script captures the data automatically. Takes a few seconds.

For **dashboards**: the script paginates through all rows — you will see a counter like `2,000 rows fetched … 4,000 rows fetched …`. For large tables (50,000+ rows) this can take a few minutes.

If the dashboard has **multiple tables**, the script lists them and asks you to type the number of the one you want.

### Step 6 — Find your CSV

When it finishes you will see:

```
✓  88,798 rows × 10 columns → /Users/yourname/Desktop/My_Report_20260825_143022.csv
```

The file is on your **Desktop**, named after the report/table with a timestamp.

---

## Passing the URL directly (optional shortcut)

Instead of being prompted, you can paste the URL right in the command:

```
python3 scrape_sf.py "https://ibmsc.lightning.force.com/lightning/r/Report/00OgR000000WWzFUAW/view"
```

---

## Troubleshooting

**"No rows captured" / script times out**  
The report or table did not finish loading. Make sure:
- The report fully loaded in the browser (you can see the data)
- You are still logged in
- If the report has a filter or date prompt, fill it in the browser first, then re-run

**"runReport response not captured" for a report**  
The URL must be a standard Lightning Report (address bar contains `/lightning/r/Report/`). CRM Analytics dashboards use a different URL — if that's what you have, the script will also handle it automatically.

**Dashboard table not found / 90-second timeout**  
The table widget may not have loaded yet. Scroll the dashboard so the table is visible in the browser window and re-run.

**Token expired mid-export (HTTP 401/403)**  
For large dashboard exports, session tokens can expire. The script will print a message asking you to scroll the table in the browser — this triggers a fresh request and the export continues automatically from where it left off.

**"playwright is not installed" error**  
Run the setup commands again:
```
pip install playwright
playwright install chromium
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

## Notes

- The CSV is saved to your **Desktop** automatically. You can change the save location with `-o`:
  ```
  python3 scrape_sf.py "https://..." -o ~/Documents/my_export.csv
  ```
- **No credentials are ever stored** — the script uses your existing browser session.
- The script opens a real browser window so Salesforce sees a normal login. Nothing is automated through the login; you always log in yourself.
- For dashboards, the Wave API sometimes returns the same account linked to multiple territories/coverage models. The script automatically removes 100%-identical duplicate rows while keeping rows that genuinely differ (e.g. same account in multiple coverage models).
