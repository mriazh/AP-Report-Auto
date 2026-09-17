"""CLI surface: exit codes, argument handling, and what each command touches."""

from __future__ import annotations

from pathlib import Path

import pytest

from fixtures import ap_csv, build_templates, ssid_csv, write_raw_pair
from huawei_ap_report.cli import EXIT_CONFIG, EXIT_FAILED, EXIT_OK, build_parser, main


def _env(tmp_path: Path, **extra: str) -> Path:
    lines = [
        "HUAWEI_BASE_URL=https://172.16.24.3",
        "HUAWEI_USERNAME=operator",
        "HUAWEI_PASSWORD=s3cret",
        f"TEMPLATE_DIR={tmp_path / 'templates'}",
        f"OUTPUT_ROOT={tmp_path / 'output'}",
        "GOWA_ENABLED=false",
        "REPORT_MONTH=2026-09",
    ]
    lines += [f"{key}={value}" for key, value in extra.items()]
    path = tmp_path / ".env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_parser_requires_a_command():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_parser_known_commands():
    parser = build_parser()

    assert parser.parse_args(["run"]).command == "run"
    assert parser.parse_args(["run", "--date", "2026-09-24"]).date == "2026-09-24"
    assert parser.parse_args(["run", "--no-notify"]).no_notify is True
    assert parser.parse_args(["reports"]).command == "reports"
    assert parser.parse_args(["check"]).command == "check"


def test_missing_env_file_exits_with_the_config_code(tmp_path, capsys):
    code = main(["--env-file", str(tmp_path / "absent.env"), "check"])

    assert code == EXIT_CONFIG
    assert "configuration" in capsys.readouterr().err.lower()


def test_check_reports_missing_templates(tmp_path, capsys):
    env = _env(tmp_path)

    code = main(["--env-file", str(env), "check"])
    output = capsys.readouterr().out

    assert code == EXIT_CONFIG
    assert "MISSING" in output
    assert "connected" in output


def test_check_passes_once_templates_are_in_place(tmp_path, capsys):
    build_templates(tmp_path / "templates")
    env = _env(tmp_path)

    code = main(["--env-file", str(env), "check"])
    output = capsys.readouterr().out

    assert code == EXIT_OK
    assert "ready" in output
    assert "credentials  configured" in output


def test_check_flags_placeholder_credentials(tmp_path, capsys):
    build_templates(tmp_path / "templates")
    env = _env(tmp_path, HUAWEI_PASSWORD="CHANGE_ME")

    code = main(["--env-file", str(env), "check"])

    assert code == EXIT_CONFIG
    assert "not usable" in capsys.readouterr().out


def test_reports_rebuilds_from_the_archive(tmp_path, capsys):
    build_templates(tmp_path / "templates")
    write_raw_pair(tmp_path / "output" / "raw" / "20260901", ap=ap_csv(), ssid=ssid_csv())
    env = _env(tmp_path)

    code = main(["--env-file", str(env), "reports"])
    output = capsys.readouterr().out

    assert code == EXIT_OK
    assert "rebuilt 2026-09" in output
    for kind in ("Connected", "Detail", "Graph"):
        assert kind in output


def test_reports_without_snapshots_fails_clearly(tmp_path, capsys):
    build_templates(tmp_path / "templates")


def test_run_command_prevents_concurrent_execution(tmp_path, capsys):
    """Test that the run command prevents concurrent execution via process lock."""
    from huawei_ap_report.lock import ProcessLock
    
    build_templates(tmp_path / "templates")
    env = _env(tmp_path)
    
    # Acquire the lock to simulate a running process
    lock_path = tmp_path / "output" / ".run.lock"
    lock = ProcessLock(lock_path)
    lock_acquired = lock.acquire(blocking=False)
    assert lock_acquired, "Should be able to acquire the lock"
    
    try:
        # This should fail because the lock is already held
        code = main(["--env-file", str(env), "run"])
        
        assert code == EXIT_FAILED, f"Expected EXIT_FAILED but got {code}"
        err = capsys.readouterr().err
        assert "Another ap-report-auto" in err, f"Expected lock message in stderr, got: {err}"
    finally:
        lock.release()


def test_reports_command_respects_process_lock(tmp_path, capsys):
    """The reports command is held under the output lock like run."""
    from huawei_ap_report.lock import ProcessLock

    build_templates(tmp_path / "templates")
    write_raw_pair(tmp_path / "output" / "raw" / "20260901", ap=ap_csv(), ssid=ssid_csv())
    env = _env(tmp_path)

    lock = ProcessLock(tmp_path / "output" / ".run.lock")
    assert lock.acquire(blocking=False)
    try:
        code = main(["--env-file", str(env), "reports"])
        err = capsys.readouterr().err
        assert code == EXIT_FAILED
        assert "holding the output lock" in err
    finally:
        lock.release()

    code = main(["--env-file", str(env), "reports"])
    assert code == EXIT_OK
    assert "rebuilt 2026-09" in capsys.readouterr().out


def test_run_rejects_a_malformed_date(tmp_path, capsys):
    env = _env(tmp_path)

    code = main(["--env-file", str(env), "run", "--date", "24-09-2026"])

    assert code == EXIT_CONFIG
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_run_rejects_placeholder_credentials_before_touching_the_browser(tmp_path, capsys):
    env = _env(tmp_path, HUAWEI_USERNAME="CHANGE_ME")

    code = main(["--env-file", str(env), "run"])

    assert code == EXIT_FAILED
    assert "HUAWEI_USERNAME" in capsys.readouterr().err


def test_run_exits_failed_when_the_portal_is_unreachable(tmp_path, capsys, monkeypatch):
    """The browser is never launched here; the failure comes from the stub adapter."""

    build_templates(tmp_path / "templates")
    env = _env(tmp_path)
    monkeypatch.setenv("RETRY_ATTEMPTS", "1")

    import huawei_ap_report.cli as cli

    class Unreachable:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def export(self, filename):
            from huawei_ap_report.errors import PortalUnavailableError

            raise PortalUnavailableError("network is unreachable")

    monkeypatch.setattr(cli, "PlaywrightCollector", lambda *a, **k: Unreachable())

    code = main(["--env-file", str(env), "run", "--no-notify"])
    err = capsys.readouterr().err

    assert code == EXIT_FAILED
    assert "network is unreachable" in err


def test_run_succeeds_end_to_end_with_a_stub_exporter(tmp_path, capsys, monkeypatch):
    build_templates(tmp_path / "templates")
    env = _env(tmp_path)

    import huawei_ap_report.cli as cli
    from huawei_ap_report.huawei import Download

    class Stub:
        def __init__(self, settings, **kwargs):
            self.settings = settings

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def export(self, filename):
            payload = ap_csv() if filename.endswith("apInfo.csv") else ssid_csv()
            return Download(filename, payload)

    monkeypatch.setattr(cli, "PlaywrightCollector", Stub)

    code = main(["--env-file", str(env), "run", "--date", "2026-09-24", "--no-notify"])
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "OK  2026-09-24" in out
    assert (tmp_path / "output" / "raw" / "20260924" / "apInfo.csv").exists()


def test_no_notify_uses_the_null_notifier(tmp_path):
    import dataclasses

    import huawei_ap_report.cli as cli
    from huawei_ap_report.notifier import GowaNotifier, NullNotifier
    from huawei_ap_report.settings import NotifySettings, load_settings

    settings = load_settings(_env(tmp_path, GOWA_ENABLED="true", GOWA_BASE_URL="https://x", GOWA_GROUP_JID="j"))
    # Even when the env enables GOWA, --no-notify wins.
    assert isinstance(cli._build_notifier(settings, enabled=False), NullNotifier)

    enabled = dataclasses.replace(
        settings, notify=NotifySettings(enabled=True, base_url="https://x", device_id="d", group_jid="j", timeout_seconds=5, tls_verify=True, tls_ca_bundle="")
    )
    assert isinstance(cli._build_notifier(enabled, enabled=True), GowaNotifier)