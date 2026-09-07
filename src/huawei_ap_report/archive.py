"""Paired raw snapshot archive.

One day's collection is only useful as a complete ``apInfo.csv`` + ``ssidInfo.csv``
pair, so publication is directory-swapped: both files are written and validated
in a staging directory first, and only then does ``output/raw/YYYYMMDD/`` start
pointing at the new pair. A failed or partial download therefore never replaces
a good snapshot and never leaves half a day behind.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .errors import ExportValidationError
from .exports import AP_FILENAME, SSID_FILENAME, TableExport, load_ap_export, load_ssid_export, parse_export
from .exports import AP_COLUMNS, SSID_COLUMNS

logger = logging.getLogger(__name__)

DAY_DIR_RE = re.compile(r"^(\d{8})$")
RAW_SUBDIR = "raw"


@dataclass(frozen=True)
class RawSnapshot:
    """A validated ``apInfo.csv``/``ssidInfo.csv`` pair for one local day."""

    day: date
    directory: Path
    ap: TableExport
    ssid: TableExport

    @property
    def day_compact(self) -> str:
        return self.day.strftime("%Y%m%d")


def raw_root(output_root: Path) -> Path:
    return Path(output_root) / RAW_SUBDIR


def snapshot_dir(output_root: Path, day: date) -> Path:
    return raw_root(output_root) / day.strftime("%Y%m%d")


def parse_day_dirname(name: str) -> date | None:
    """Parse a ``YYYYMMDD`` directory name into a date, or ``None``."""

    match = DAY_DIR_RE.match(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def list_snapshot_days(output_root: Path) -> list[date]:
    """All parseable snapshot days under ``output/raw``, ascending."""

    root = raw_root(output_root)
    if not root.is_dir():
        return []
    days = [parse_day_dirname(entry.name) for entry in root.iterdir() if entry.is_dir()]
    return sorted(day for day in days if day is not None)


def load_snapshot(directory: Path) -> RawSnapshot:
    """Load and validate one day's pair; raises when the pair is incomplete."""

    directory = Path(directory)
    day = parse_day_dirname(directory.name)
    if day is None:
        raise ExportValidationError(f"not a dated snapshot directory: {directory}")
    ap = load_ap_export(directory / AP_FILENAME)
    ssid = load_ssid_export(directory / SSID_FILENAME)
    return RawSnapshot(day=day, directory=directory, ap=ap, ssid=ssid)


def collect_snapshots(output_root: Path, *, month: tuple[int, int] | None = None) -> tuple[list[RawSnapshot], list[tuple[date, str]]]:
    """Load valid snapshots, optionally restricted to ``(year, month)``.

    Returns the usable snapshots plus a ``(day, reason)`` list for days that were
    skipped, so the caller can log them instead of silently generating reports
    from partial data.
    """

    snapshots: list[RawSnapshot] = []
    skipped: list[tuple[date, str]] = []
    for day in list_snapshot_days(output_root):
        if month is not None and (day.year, day.month) != month:
            continue
        try:
            snapshots.append(load_snapshot(raw_root(output_root) / day.strftime("%Y%m%d")))
        except ExportValidationError as exc:
            logger.warning("skipping raw snapshot %s: %s", day.isoformat(), exc)
            skipped.append((day, str(exc)))
    return snapshots, skipped


def validate_pair(ap_bytes: bytes, ssid_bytes: bytes) -> tuple[TableExport, TableExport]:
    """Validate both exports before anything is written to the archive."""

    ap = parse_export(ap_bytes.decode("utf-8-sig"), kind="AP", required_columns=AP_COLUMNS)
    ssid = parse_export(ssid_bytes.decode("utf-8-sig"), kind="SSID", required_columns=SSID_COLUMNS)
    return ap, ssid


def publish_pair(output_root: Path, day: date, *, ap_bytes: bytes, ssid_bytes: bytes) -> RawSnapshot:
    """Validate then atomically publish one day's pair.

    Re-running the same day replaces the pair wholesale, so the dated folder
    never accumulates stale downloads.
    """

    ap, ssid = validate_pair(ap_bytes, ssid_bytes)
    target = snapshot_dir(output_root, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".staging-{target.name}-{uuid.uuid4().hex[:8]}"
    backup = target.parent / f".replaced-{target.name}-{uuid.uuid4().hex[:8]}"
    try:
        staging.mkdir()
        (staging / AP_FILENAME).write_bytes(ap_bytes)
        (staging / SSID_FILENAME).write_bytes(ssid_bytes)
        if target.exists():
            os.replace(target, backup)
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    shutil.rmtree(backup, ignore_errors=True)
    logger.info("published raw snapshot %s (%d AP rows, %d SSID rows)", day.isoformat(), len(ap), len(ssid))
    return RawSnapshot(day=day, directory=target, ap=ap, ssid=ssid)