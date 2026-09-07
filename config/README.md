# Report templates (operator action required)

The three monthly workbooks are generated from **template workbooks that are not
stored in this repository**. They are your files, they stay local, and they are
git-ignored.

## Portal credentials and selectors (config/.env)

`config/.env` holds one real Huawei username/password pair (used once per daily
session) and a set of CSS/Playwright locators that identify the portal's login
form, the AP/SSID sidebar entries, and the two distinct export controls. These
locators are **not** credentials — they are DOM selectors observed in the
operator's saved portal pages (static evidence only, not live-validated). The
example file `config/.env.example` documents the observed values; fill in the
real credentials and keep the selectors unless the portal UI changes.

| Setting | Kind | Purpose |
| ------- | ---- | ------- |
| `HUAWEI_USERNAME` / `HUAWEI_PASSWORD` | Credential | The one login pair for the whole daily run. |
| `HUAWEI_SEL_LOGIN_USERNAME` / `HUAWEI_SEL_LOGIN_PASSWORD` / `HUAWEI_SEL_LOGIN_SUBMIT` | Selector | Login form inputs and submit button. |
| `HUAWEI_SEL_NAV_AP` / `HUAWEI_SEL_EXPORT_AP` | Selector | AP view sidebar entry and its export button. |
| `HUAWEI_SEL_NAV_SSID` / `HUAWEI_SEL_EXPORT_SSID` | Selector | SSID view sidebar entry and its export button. |

The old `HUAWEI_SEL_EXPORT_BUTTON` (one shared export selector) is no longer
used — the AP and SSID dashboards have different controls.

## What to place here

Copy the three example workbooks into `config/templates/` and keep any name that
contains the report keyword — the generator discovers templates by keyword, not by
an exact file name:

| Report     | File must contain | Typical name                      |
| ---------- | ----------------- | --------------------------------- |
| `Connected` | `connected`      | `Report_Connected_AP_Huawei.xlsx` |
| `Detail`    | `detail`         | `Report_Detail_AP_Huawei.xlsx`    |
| `Graph`     | `graph`          | `Report_Graph_AP_Huawei.xlsx`     |

Matching is case-insensitive and accepts other words in the name
(`2026_09-Report_Connected_AP_Huawei.xlsx` works). If a directory contains more
than one candidate for a report, the newest file wins and the resolved path is
logged — check the log line after the first run.

Override the directory with `TEMPLATE_DIR` in `config/.env` if you keep the
templates elsewhere.

## Required workbook shape

The generator writes into existing tabs and keeps whatever the template already
defines (titles, headers, styles, charts, hidden sheets, formulas).

### `Connected` template
Tabs `Master`, one tab per day (`01` … `31`) and `Monthly`.
Title in `A2`, header row in `A4`, data from `A5`:
`SSID`, `User Quantity`, `AP Quantity`, `Valid Throughput (bps) ↓↑`,
`Frame quantity ↓↑`, `Downlink retransmission ratio`,
`Downlink packet loss ratio`.
`Master` is intentionally left as the template has it (in the supplied example it
holds the title and header only).

### `Detail` template
Tabs `Master`, one per day (`01` … `31`) and `Monthly`.
Header row `1`, data from `A2`, the same 28 AP columns as the raw export.
`Master` holds the header only in the supplied example.

### `Graph` template
Tabs `Master`, one per day (`01` … `31`), `Monthly`, plus the hidden helper
sheets `__GraphData` (long-format AP rows with a `Report Date` column) and
`__GraphSummary` (per-date bucket counts and KPI columns, last row
`Monthly Average`). Each visible tab keeps its KPI cells (`B6`, `F6`, `J6`,
`N6`), bucket labels in row `53`, and the chart source formulas in row `54`; the
generator rewrites row `54` formulas to point at the right `__GraphSummary`
rows.

The visible note in the supplied Graph workbook tells the operator to paste data
into a sheet called `Raw Data`. **No `Raw Data` sheet exists** — the real inputs
are the hidden `__GraphData` and `__GraphSummary` sheets, and that is what this
tool writes. The note is left untouched because it is template text.

## Day tabs for longer months

A 30-day template has no `31` tab. Missing day tabs are created by copying the
template's last existing day tab, so values, styles and headers come along.
Charts are not cloned, so a newly created Graph day tab has no charts — add the
tab (with its charts) to the template instead if you need charts for day 31.