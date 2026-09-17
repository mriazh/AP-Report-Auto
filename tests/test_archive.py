from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from fixtures import ap_csv, ap_record, ssid_csv, ssid_record, write_raw_pair
from huawei_ap_report.archive import (
    collect_snapshots,
    list_snapshot_days,
    load_snapshot,
    parse_day_dirname,
    publish_pair,
    raw_root,
    recover_dangling_backups,
    snapshot_dir,
    validate_pair,
)
from huawei_ap_report.errors import ExportValidationError


def test_publish_pair_creates_dated_directory(tmp_path):
    day = date(2026, 9, 24)

    snapshot = publish_pair(tmp_path, day, ap_bytes=ap_csv(), ssid_bytes=ssid_csv())

    directory = tmp_path / "raw" / "20260924"
    assert snapshot.directory == directory
    assert sorted(path.name for path in directory.iterdir()) == ["apInfo.csv", "ssidInfo.csv"]
    assert snapshot.day_compact == "20260924"


def test_publish_pair_is_idempotent_and_leaves_no_staging(tmp_path):
    day = date(2026, 9, 24)
    publish_pair(tmp_path, day, ap_bytes=ap_csv(), ssid_bytes=ssid_csv())

    publish_pair(
        tmp_path,
        day,
        ap_bytes=ap_csv([ap_record(ap_id=1, name="NEW-AP")]),
        ssid_bytes=ssid_csv([ssid_record("NEW-SSID")]),
    )

    directory = snapshot_dir(tmp_path, day)
    assert sorted(entry.name for entry in directory.iterdir()) == ["apInfo.csv", "ssidInfo.csv"]
    snapshot = load_snapshot(directory)
    assert snapshot.ap.records[0]["AP name"] == "NEW-AP"
    assert [entry.name for entry in directory.parent.iterdir() if entry.name != directory.name] == []


def test_invalid_ssid_never_replaces_an_existing_pair(tmp_path):
    day = date(2026, 9, 24)
    publish_pair(tmp_path, day, ap_bytes=ap_csv(), ssid_bytes=ssid_csv())
    good = (tmp_path / "raw" / "20260924" / "ssidInfo.csv").read_bytes()

    with pytest.raises(ExportValidationError):
        publish_pair(tmp_path, day, ap_bytes=ap_csv([ap_record(ap_id=9, name="PARTIAL")]), ssid_bytes=b"\xef\xbb\xbf")

    assert (tmp_path / "raw" / "20260924" / "ssidInfo.csv").read_bytes() == good
    assert load_snapshot(tmp_path / "raw" / "20260924").ap.records[0]["AP name"] == "AP-TEST-01"
    assert [entry.name for entry in (tmp_path / "raw").iterdir()] == ["20260924"]


def test_missing_ssid_leaves_no_partial_day(tmp_path):
    day = date(2026, 9, 25)

    with pytest.raises(ExportValidationError, match="no data rows|empty"):
        publish_pair(tmp_path, day, ap_bytes=ap_csv(), ssid_bytes=b"\xef\xbb\xbf")

    assert not snapshot_dir(tmp_path, day).exists()
    assert list_snapshot_days(tmp_path) == []


def test_list_snapshot_days_ignores_foreign_directories(tmp_path):
    write_raw_pair(raw_root(tmp_path) / "20260901")
    write_raw_pair(raw_root(tmp_path) / "20260902")
    (raw_root(tmp_path) / "notes").mkdir()
    (raw_root(tmp_path) / "2026-09-03").mkdir()

    assert list_snapshot_days(tmp_path) == [date(2026, 9, 1), date(2026, 9, 2)]


def test_parse_day_dirname_rejects_bad_names():
    assert parse_day_dirname("20260901") == date(2026, 9, 1)
    assert parse_day_dirname("20261301") is None
    assert parse_day_dirname("2026-09-01") is None


def test_collect_snapshots_filters_by_month_and_skips_broken_days(tmp_path):
    write_raw_pair(raw_root(tmp_path) / "20260901", ap=ap_csv([ap_record(ap_id=1, name="SEP-1")]))
    write_raw_pair(raw_root(tmp_path) / "20260902")
    # Break the pair for 20260903 by removing the SSID export.
    broken = write_raw_pair(raw_root(tmp_path) / "20260903")
    (broken / "ssidInfo.csv").unlink()
    write_raw_pair(raw_root(tmp_path) / "20261001", ap=ap_csv([ap_record(ap_id=9, name="OCT-1")]))

    september, skipped = collect_snapshots(tmp_path, month=(2026, 9))

    assert [snapshot.day for snapshot in september] == [date(2026, 9, 1), date(2026, 9, 2)]
    assert [day for day, _ in skipped] == [date(2026, 9, 3)]


def test_load_snapshot_rejects_undated_directory(tmp_path):
    directory = write_raw_pair(Path(tmp_path) / "not-a-date")

    with pytest.raises(ExportValidationError, match="dated snapshot"):
        load_snapshot(directory)


def test_validate_pair_rejects_invalid_encoding(tmp_path):
    """Non-UTF-8 bytes raise ExportValidationError through validate_pair."""
    valid = ap_csv()
    bad = b"\xff\xfe\x00\x01garbage"
    with pytest.raises(ExportValidationError, match="invalid UTF-8"):
        validate_pair(valid, bad)
    with pytest.raises(ExportValidationError, match="invalid UTF-8"):
        validate_pair(bad, valid)


def test_recover_dangling_backups_restores_missing_target(tmp_path):
    raw = raw_root(tmp_path)
    raw.mkdir()
    # Simulate an interrupted publish: target 20260924 missing, backup present.
    backup = raw / ".replaced-20260924-abc123"
    backup.mkdir()
    (backup / "apInfo.csv").write_bytes(b"")
    (backup / "ssidInfo.csv").write_bytes(b"")
    # Stale staging folder to clean up.
    (raw / ".staging-20260924-def456").mkdir()

    recover_dangling_backups(raw)

    assert (raw / "20260924").is_dir()
    assert not (raw / ".replaced-20260924-abc123").exists()
    assert not (raw / ".staging-20260924-def456").exists()


def test_recover_dangling_backups_leaves_backup_when_target_exists(tmp_path):
    raw = raw_root(tmp_path)
    raw.mkdir()
    target = raw / "20260924"
    target.mkdir()
    (target / "apInfo.csv").write_bytes(b"ok")
    backup = raw / ".replaced-20260924-abc123"
    backup.mkdir()
    (backup / "apInfo.csv").write_bytes(b"old")

    recover_dangling_backups(raw)

    assert (target / "apInfo.csv").read_bytes() == b"ok"
    assert backup.exists()  # target already present, backup is a normal leftover


def test_recover_dangling_backups_noop_when_raw_missing(tmp_path):
    recover_dangling_backups(raw_root(tmp_path))  # does not raise