# AP Report Auto

Daily automation for the office Huawei AP dashboard: export the AP and SSID CSVs
at 10:00 WIB, archive them by local export date, rebuild three monthly Excel
reports from them, and post a start/success/failure notice to a WhatsApp group.

Runs on the office Debian PC under a systemd timer. Everything here works offline
except the two portal calls, which only happen during `run`.

## What a run does

1. Send the "starting" notice to the WhatsApp group.
2. Sign in to the portal, export `apInfo.csv` and `ssidInfo.csv`
   (bounded retries with exponential backoff).
3. Validate both files, then publish them as one complete pair to
   `output/raw/YYYYMMDD/`.
4. Rebuild the current month's three reports from every valid raw day and publish
   them as a set.
5. Send the success notice — or the failure notice, having left the previous
   raw day and the previous reports untouched.

A failed or partial collection never publishes a raw pair and never regenerates
reports, so the last good reports stay readable.

## Layout

```
config/.env                 local secrets and settings (git-ignored)
config/templates/           the three report templates you supply (git-ignored)
src/huawei_ap_report/       the package
deploy/systemd/             service + timer units
tests/                      offline test suite
output/raw/YYYYMMDD/        archived apInfo.csv + ssidInfo.csv
output/report/              YYYY_MM-Report_{Connected,Detail,Graph}_AP_Huawei.xlsx
```

## Requirements

- Debian with Python 3.11+
- Chromium via Playwright (`ap-report-auto install-browser`)
- The three template workbooks — see `config/README.md`

## Install (Debian)

The repo lives under the operator's own home directory, matching the other
GitHub projects on this PC (e.g. `MRTG-CMP`), rather than under a system
directory. Clone into `~/Github-PC/AP-Report-Auto` as the `mriazh` user:

```sh
sudo -u mriazh mkdir -p /home/mriazh/Github-PC
sudo -u mriazh git clone <repo> /home/mriazh/Github-PC/AP-Report-Auto
sudo -u mriazh python3 -m venv /home/mriazh/Github-PC/AP-Report-Auto/.venv
sudo -u mriazh /home/mriazh/Github-PC/AP-Report-Auto/.venv/bin/pip install '/home/mriazh/Github-PC/AP-Report-Auto/[browser]'
sudo -u mriazh /home/mriazh/Github-PC/AP-Report-Auto/.venv/bin/playwright install chromium
```

A more typical interactive form, run as `mriazh`:

```sh
cd /home/mriazh/Github-PC/AP-Report-Auto
python3 -m venv .venv
.venv/bin/pip install '.[browser]'
.venv/bin/playwright install chromium
```

Copy the example env and fill it in locally. It is never committed:

```sh
sudo -u mriazh cp /home/mriazh/Github-PC/AP-Report-Auto/config/.env.example /home/mriazh/Github-PC/AP-Report-Auto/config/.env
sudo -u mriazh chmod 600 /home/mriazh/Github-PC/AP-Report-Auto/config/.env
$EDITOR /home/mriazh/Github-PC/AP-Report-Auto/config/.env
```

Place the three report templates in `config/templates/` (see `config/README.md`),
then confirm the setup:

```sh
sudo -u mriazh /home/mriazh/Github-PC/AP-Report-Auto/.venv/bin/ap-report-auto check
```

## Schedule

```sh
sudo cp deploy/systemd/ap-report-auto.service /etc/systemd/system/
sudo cp deploy/systemd/ap-report-auto.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ap-report-auto.timer
systemctl list-timers ap-report-auto.timer
```

The timer pins `Timezone=Asia/Jakarta` and fires at 10:00 WIB every day. If the
PC is off at 10:00 the run happens once at the next boot — there is no catch-up
for days that were missed entirely.

Check what the host thinks the schedule is:

```sh
sudo systemd-analyze calendar '*-*-* 10:00:00'
```

## Manual runs

```sh
# Full run for today, with notices
.venv/bin/ap-report-auto run

# Full run for a specific date, no notices (useful when testing the reports)
.venv/bin/ap-report-auto run --date 2026-09-24 --no-notify

# Rebuild this month's reports from the archive, without touching the portal
.venv/bin/ap-report-auto reports

# Verify config, credentials and templates; collects nothing
.venv/bin/ap-report-auto check
```

Exit codes: `0` success, `1` job failure, `2` configuration problem.

## Backfilling a month

Copy an existing raw day into `output/raw/YYYYMMDD/` and rerun `reports`. Each
directory needs both `apInfo.csv` and `ssidInfo.csv`; a half-populated day is
skipped and logged.

```sh
mkdir -p output/raw/20260901
cp /path/to/apInfo.csv /path/to/ssidInfo.csv output/raw/20260901/
.venv/bin/ap-report-auto reports
```

## Development

```sh
python -m pytest              # offline; no portal, no credentials
```

The suite uses synthetic fixtures that reproduce the shape of the portal exports
and the template layout (BOM, tab-prefixed cells, tab sets, hidden Graph sheets)
without copying any sample file. Tests do not need `config/.env` and never log
credentials.

## Notes and limits

- **TLS verification is on by default.** The portal at `https://172.16.24.3` uses a
  self-signed internal certificate. To connect, set `TLS_VERIFY=false` in
  `config/.env` (scopes `ignore_https_errors=True` to the browser context only),
  or provide a CA bundle via `TLS_CA_BUNDLE` for enterprise CAs. The launch
  itself never relaxes TLS — only the per-job context does.
- **Graph data area.** The Graph template's visible note mentions a `Raw Data`
  sheet, which does not exist in the workbook. The real inputs are the hidden
  `__GraphData` and `__GraphSummary` sheets, and those are what this tool writes;
  see `config/README.md` and `docs/design.md`.
- **New day tabs.** A 30-day template has no `31` tab. Missing tabs are created by
  copying the last day tab, which does not copy charts — add the tab to the
  template if you need charts for day 31.
- **Chart formulas are rewritten, values are not recalculated.** The KPI cells and
  the chart source row keep their formulas, but Excel or LibreOffice has to open
  the file once to compute the new results. Charts render from the formulas, so
  this happens on first open.