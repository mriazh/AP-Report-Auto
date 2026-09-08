"""End-to-end job behaviour, offline: no portal, no credentials, no gateway."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from fixtures import ap_csv, ap_record, build_templates, ssid_csv, ssid_record
from huawei_ap_report.errors import CollectionError, ReportError
from huawei_ap_report.huawei import Download, PortalExporter
from huawei_ap_report.job import DailyJob, rebuild_only
from huawei_ap_report.notifier import NullNotifier, failure_message, started_message, success_message
from huawei_ap_report.settings import load_settings

DAY = date(2026, 9, 24)


class ScriptedExporter(PortalExporter):
    def __init__(self, results):
        self.results = list(results)
        self.calls: list[str] = []

    def export(self, filename: str) -> Download:
        self.calls.append(filename)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class RecordingNotifier:
    def __init__(self, *, fails: bool = False):
        self.messages: list[str] = []
        self.fails = fails

    def send(self, message: str) -> bool:
        if self.fails:
            raise RuntimeError("gateway down")
        self.messages.append(message)
        return True


def _env(tmp_path: Path, *, report_month: str | None = "2026-09", **extra: str) -> Path:
    lines = [
        "HUAWEI_BASE_URL=https://172.16.24.3",
        "HUAWEI_USERNAME=operator",
        "HUAWEI_PASSWORD=s3cret",
        f"TEMPLATE_DIR={tmp_path / 'templates'}",
        f"OUTPUT_ROOT={tmp_path / 'output'}",
        "RETRY_ATTEMPTS=2",
        "RETRY_BACKOFF_SECONDS=1",
    ]
    if report_month is not None:
        lines.append(f"REPORT_MONTH={report_month}")
    lines += [f"{key}={value}" for key, value in extra.items()]
    path = tmp_path / ".env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _job(tmp_path: Path, results, notifier=None, *, report_month: str | None = "2026-09"):
    settings = load_settings(_env(tmp_path, report_month=report_month))
    return DailyJob(settings, ScriptedExporter(results), notifier or RecordingNotifier(), sleep=lambda _: None)


def _ok_results(users: int = 55):
    return [
        Download("apInfo.csv", ap_csv([ap_record(0, "AP-1")])),
        Download("ssidInfo.csv", ssid_csv([ssid_record("A", users=users)])),
    ]


def test_successful_run_archives_and_rebuilds(tmp_path):
    build_templates(tmp_path / "templates")
    notifier = RecordingNotifier()
    result = _job(tmp_path, _ok_results(), notifier).run(DAY)

    assert result.ok
    assert result.snapshot.directory == tmp_path / "output" / "raw" / "20260924"
    assert sorted(result.report_names) == [
        "2026_09-Report_Connected_AP_Huawei.xlsx",
        "2026_09-Report_Detail_AP_Huawei.xlsx",
        "2026_09-Report_Graph_AP_Huawei.xlsx",
    ]
    assert len(notifier.messages) == 2
    assert "[Huawei AP Report Automation] START | mode=daily | date=2026-09-24" in notifier.messages[0]
    assert "[Huawei AP Report Automation] SUCCESS | mode=daily | date=2026-09-24" in notifier.messages[1]


def test_failed_export_does_not_archive_or_regenerate(tmp_path):
    build_templates(tmp_path / "templates")

    # A previous good day and its reports.
    first = _job(tmp_path, _ok_results()).run(date(2026, 9, 23))
    assert first.ok
    before = {path.name: path.read_bytes() for path in first.reports}
    raw_before = sorted(path.name for path in (tmp_path / "output" / "raw" / "20260923").iterdir())

    notifier = RecordingNotifier()
    result = _job(tmp_path, [CollectionError("portal unreachable")] * 4, notifier).run(DAY)

    assert not result.ok
    assert "failed after 2 attempts" in result.error
    assert not (tmp_path / "output" / "raw" / "20260924").exists()
    assert "20260924" not in [entry.name for entry in (tmp_path / "output" / "raw").iterdir()]
    assert sorted(path.name for path in (tmp_path / "output" / "raw" / "20260923").iterdir()) == raw_before
    for path in first.reports:
        assert path.read_bytes() == before[path.name]
    assert len(notifier.messages) == 2
    assert "[Huawei AP Report Automation] FAILED | mode=daily | date=2026-09-24" in notifier.messages[-1]


def test_first_export_ok_second_invalid_leaves_no_half_day(tmp_path):
    build_templates(tmp_path / "templates")
    result = _job(tmp_path, [Download("apInfo.csv", ap_csv()), Download("ssidInfo.csv", b"\xef\xbb\xbf")]).run(DAY)

    assert not result.ok
    assert not (tmp_path / "output" / "raw" / "20260924").exists()


def test_report_failure_still_archives_the_raw_day(tmp_path):
    # No templates at all: collection succeeds, report generation cannot.
    result = _job(tmp_path, _ok_results()).run(DAY)

    assert not result.ok
    assert result.snapshot is not None
    assert (tmp_path / "output" / "raw" / "20260924" / "apInfo.csv").exists()
    assert "template" in result.error


def test_broken_notifier_never_masks_a_successful_run(tmp_path):
    build_templates(tmp_path / "templates")
    notifier = RecordingNotifier(fails=True)

    result = _job(tmp_path, _ok_results(), notifier).run(DAY)

    assert result.ok
    assert notifier.messages == []


def test_broken_notifier_never_masks_a_failure(tmp_path):
    build_templates(tmp_path / "templates")
    result = _job(tmp_path, [CollectionError("down")] * 4, RecordingNotifier(fails=True)).run(DAY)

    assert not result.ok


def test_rerunning_the_same_day_is_deterministic(tmp_path):
    build_templates(tmp_path / "templates")
    first = _job(tmp_path, _ok_results()).run(DAY)
    second = _job(tmp_path, _ok_results()).run(DAY)

    assert sorted(path.name for path in (tmp_path / "output" / "raw" / "20260924").iterdir()) == [
        "apInfo.csv",
        "ssidInfo.csv",
    ]
    assert [path.name for path in first.reports] == [path.name for path in second.reports]
    assert first.report_names == second.report_names


def test_reports_use_every_valid_day_of_the_month(tmp_path):
    build_templates(tmp_path / "templates")
    settings = load_settings(_env(tmp_path))
    _job(tmp_path, _ok_results(users=10)).run(date(2026, 9, 1))
    _job(tmp_path, _ok_results(users=30)).run(date(2026, 9, 2))

    from openpyxl import load_workbook

    paths = rebuild_only(settings, (2026, 9))
    connected = load_workbook([path for path in paths if "Connected" in path.name][0])
    # Day 1 and 2 each have one SSID with 10 and 30 users -> 20 in Monthly.
    assert connected["01"]["B5"].value == 10
    assert connected["02"]["B5"].value == 30
    assert connected["Monthly"]["B5"].value == pytest.approx(20.0)


def test_rebuild_only_does_not_touch_the_portal(tmp_path):
    build_templates(tmp_path / "templates")
    settings = load_settings(_env(tmp_path))
    _job(tmp_path, _ok_results()).run(DAY)

    paths = rebuild_only(settings, (2026, 9))

    assert len(paths) == 3


def test_rebuild_only_without_snapshots_fails(tmp_path):
    build_templates(tmp_path / "templates")
    settings = load_settings(_env(tmp_path))

    with pytest.raises(ReportError, match="no valid raw snapshots"):
        rebuild_only(settings, (2026, 9))


def test_month_rollover_uses_the_new_month(tmp_path):
    build_templates(tmp_path / "templates")
    result = _job(tmp_path, _ok_results(), report_month=None).run(date(2026, 10, 1))

    assert result.month == (2026, 10)
    assert all("2026_10" in name for name in result.report_names)


def test_null_notifier_is_a_no_op():
    assert NullNotifier().send("anything") is False


def test_message_text_states_the_day_month_and_outcome():
    assert "[Huawei AP Report Automation] START | mode=daily | date=2026-09-24" in started_message(DAY, (2026, 9), mode="daily")
    success = success_message(DAY, (2026, 9), ["2026_09-Report_Detail_AP_Huawei.xlsx"], mode="daily")
    assert "SUCCESS" in success and "2026-09-24" in success
    failure = failure_message(DAY, "portal unreachable", attempts=5, mode="daily")
    assert "FAILED" in failure and "2026-09-24" in failure and "portal unreachable" in failure
    assert "FAILED" in failure_message(DAY, "boom", mode="daily")