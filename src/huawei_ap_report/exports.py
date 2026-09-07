"""Parsing and validation of the portal CSV exports.

The portal writes UTF-8 CSV with a BOM, every header and cell prefixed by a tab,
and human-readable values (``79%``, ``7.8M/3.1M``, ``<1%(1/61369)``). Parsing
strips the BOM and the tabs and keeps the values verbatim, so the same strings
reach the report templates exactly as the portal produced them.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .errors import ExportValidationError

AP_FILENAME = "apInfo.csv"
SSID_FILENAME = "ssidInfo.csv"

AP_COLUMNS: tuple[str, ...] = (
    "AP ID",
    "AP name",
    "Status",
    "MAC address",
    "AP group",
    "IP address",
    "AP type",
    "System version",
    "Patch version",
    "Serial Number",
    "Power Supply Mode",
    "Data Link Status",
    "Indoor/Outdoor Channel Set",
    "Installation location",
    "Longitude, Latitude",
    "Central AP ID",
    "Central AP name",
    "Central AP MAC address",
    "STA Quantity",
    "STA Access Failure Ratio",
    "Logout ratio",
    "CPU Usage",
    "Memory Usage",
    "Wired-side throughput(Kbps) ↓↑",
    "Login period",
    "Scenario",
    "Total Restart Count",
    "Poweroff restart count",
)

SSID_COLUMNS: tuple[str, ...] = (
    "SSID",
    "User Quantity",
    "AP Quantity",
    "Valid Throughput (bps) ↓↑",
    "Frame quantity ↓↑",
    "Downlink retransmission ratio",
    "Downlink packet loss ratio",
)


@dataclass(frozen=True)
class TableExport:
    """A validated export: column names plus one dict per data row."""

    kind: str
    columns: tuple[str, ...]
    records: tuple[dict[str, str], ...]

    def __len__(self) -> int:
        return len(self.records)

    def value(self, record: dict[str, str], column: str, default: str = "") -> str:
        """Column lookup that never raises on a column the portal dropped."""

        return record.get(column, default)


def decode_export(raw: bytes) -> str:
    """Decode portal bytes, tolerating the UTF-8 BOM and CRLF endings."""

    return raw.decode("utf-8-sig")


def parse_export(text: str, *, kind: str, required_columns: tuple[str, ...]) -> TableExport:
    """Parse and validate export text.

    Raises :class:`ExportValidationError` with every reason found, so a failed
    download is never mistaken for an empty-but-valid one.
    """

    if not text.strip():
        raise ExportValidationError(f"{kind} export is empty")

    rows = [row for row in csv.reader(text.splitlines()) if row]
    if not rows:
        raise ExportValidationError(f"{kind} export has no rows")

    header = tuple(cell.strip() for cell in rows[0])
    problems: list[str] = []

    missing = [column for column in required_columns if column not in header]
    if missing:
        problems.append(f"missing columns: {', '.join(missing)}")

    width = len(header)
    records: list[dict[str, str]] = []
    for index, row in enumerate(rows[1:], start=2):
        if len(row) != width:
            problems.append(f"row {index} has {len(row)} cells, expected {width}")
            continue
        records.append({column: cell.strip() for column, cell in zip(header, row, strict=True)})

    if not records and not problems:
        problems.append("no data rows")

    if problems:
        raise ExportValidationError(f"{kind} export rejected: {'; '.join(problems)}")

    return TableExport(kind=kind, columns=header, records=tuple(records))


def load_export(path: Path, *, kind: str, required_columns: tuple[str, ...]) -> TableExport:
    """Read and validate an export from disk."""

    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError as exc:
        raise ExportValidationError(f"{kind} export not found: {path}") from exc
    except OSError as exc:
        raise ExportValidationError(f"{kind} export unreadable: {path} ({exc.strerror})") from exc
    return parse_export(decode_export(raw), kind=kind, required_columns=required_columns)


def load_ap_export(path: Path) -> TableExport:
    return load_export(path, kind="AP", required_columns=AP_COLUMNS)


def load_ssid_export(path: Path) -> TableExport:
    return load_export(path, kind="SSID", required_columns=SSID_COLUMNS)


def required_columns_for(filename: str) -> tuple[tuple[str, ...], str]:
    """Return ``(required_columns, kind)`` for one of the two expected exports."""

    normalised = Path(filename).name.strip().lower()
    if normalised == AP_FILENAME.lower():
        return AP_COLUMNS, "AP"
    if normalised == SSID_FILENAME.lower():
        return SSID_COLUMNS, "SSID"
    raise ExportValidationError(f"unknown export filename: {filename!r}")


def validate_named_export(filename: str, data: bytes) -> TableExport:
    """Parse ``data`` against the column set implied by ``filename``.

    Used where the export name decides the schema, so an AP export is never
    validated against the SSID columns and vice versa.
    """

    required, kind = required_columns_for(filename)
    return parse_export(decode_export(data), kind=kind, required_columns=required)