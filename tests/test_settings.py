from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from huawei_ap_report.errors import ConfigError
from huawei_ap_report.settings import Settings, load_settings, local_timezone

BASE_ENV = """
HUAWEI_BASE_URL=https://172.16.24.3
HUAWEI_USERNAME=operator
HUAWEI_PASSWORD=s3cret
GOWA_BASE_URL=https://gateway.example
GOWA_GROUP_JID=1203630@g.us
TEMPLATE_DIR=config/templates
OUTPUT_ROOT=output
"""


def _write(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / ".env"
    path.write_text(BASE_ENV + extra, encoding="utf-8")
    return path


def test_loads_values_from_env_file(tmp_path):
    settings = load_settings(_write(tmp_path))

    assert settings.huawei.base_url == "https://172.16.24.3"
    assert settings.huawei.login_url == "https://172.16.24.3/view/login.html"
    assert settings.output_root == Path("output")
    assert settings.timezone_name == "Asia/Jakarta"


def test_missing_env_file_is_reported(tmp_path):
    with pytest.raises(ConfigError, match="config/.env.example"):
        load_settings(tmp_path / "absent.env")


def test_process_environment_overrides_file(tmp_path):
    settings = load_settings(_write(tmp_path, "RETRY_ATTEMPTS=2\n"), environ={"RETRY_ATTEMPTS": "7"})

    assert settings.retry.attempts == 7


def test_retry_backoff_is_exponential_and_capped(tmp_path):
    settings = load_settings(
        _write(tmp_path, "RETRY_ATTEMPTS=5\nRETRY_BACKOFF_SECONDS=10\nRETRY_MAX_BACKOFF_SECONDS=40\n")
    )

    assert [settings.retry.delay_for(attempt) for attempt in range(1, 6)] == [10, 20, 40, 40, 40]


def test_retry_bounds_are_validated(tmp_path):
    with pytest.raises(ConfigError, match="RETRY_ATTEMPTS"):
        load_settings(_write(tmp_path, "RETRY_ATTEMPTS=0\n"))
    with pytest.raises(ConfigError, match="RETRY_BACKOFF_SECONDS"):
        load_settings(_write(tmp_path, "RETRY_BACKOFF_SECONDS=-1\n"))


def test_tls_verification_defaults_on_and_cannot_be_silently_disabled(tmp_path):
    assert load_settings(_write(tmp_path)).tls_verify is True
    assert load_settings(_write(tmp_path, "TLS_VERIFY=true\n")).tls_verify is True
    # An explicit operator choice is honoured, but it has to be spelled out.
    assert load_settings(_write(tmp_path, "TLS_VERIFY=false\n")).tls_verify is False
    with pytest.raises(ConfigError, match="TLS_VERIFY"):
        load_settings(_write(tmp_path, "TLS_VERIFY=maybe\n"))


def test_ca_bundle_is_configurable_for_the_internal_certificate(tmp_path):
    settings = load_settings(_write(tmp_path, "TLS_CA_BUNDLE=/etc/ssl/office.pem\n"))

    assert settings.tls_ca_bundle == "/etc/ssl/office.pem"


def test_report_month_override_parses_several_separators(tmp_path):
    assert load_settings(_write(tmp_path, "REPORT_MONTH=2026-09\n")).report_month == (2026, 9)
    assert load_settings(_write(tmp_path, "REPORT_MONTH=2026_09\n")).report_month == (2026, 9)
    assert load_settings(_write(tmp_path)).report_month is None
    with pytest.raises(ConfigError, match="YYYY-MM"):
        load_settings(_write(tmp_path, "REPORT_MONTH=2026-13-01\n"))


def test_month_of_uses_configured_timezone(tmp_path):
    settings = load_settings(_write(tmp_path, "TIMEZONE=Asia/Jakarta\n"))
    moment = datetime(2026, 9, 30, 23, 0)

    assert settings.month_of(moment) == (2026, 9)
    # 17:00 UTC is already the next day in WIB.
    utc_moment = datetime(2026, 9, 30, 17, 0, tzinfo=settings.tzinfo)
    assert settings.month_of(utc_moment) == (2026, 9)
    assert settings.now().tzinfo is not None


def test_local_timezone_is_wib_and_rejects_unknown_names():
    moment = datetime(2026, 9, 24, 10, 0)
    assert local_timezone("Asia/Jakarta").utcoffset(moment).total_seconds() == 7 * 3600
    with pytest.raises(ConfigError, match="unknown timezone"):
        local_timezone("Mars/Olympus")


def test_verify_configured_rejects_placeholders(tmp_path):
    settings = load_settings(_write(tmp_path, "HUAWEI_USERNAME=CHANGE_ME\n"))

    with pytest.raises(ConfigError, match="HUAWEI_USERNAME"):
        settings.verify_configured()


def test_verify_configured_rejects_identical_export_names(tmp_path):
    settings = load_settings(_write(tmp_path, "AP_EXPORT_FILENAME=data.csv\nSSID_EXPORT_FILENAME=data.csv\n"))

    with pytest.raises(ConfigError, match="must differ"):
        settings.verify_configured()


def test_verify_configured_accepts_a_complete_file(tmp_path):
    load_settings(_write(tmp_path)).verify_configured()


def test_env_file_ignores_comments_and_export_prefix(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# comment\n\nexport LOG_LEVEL=DEBUG\n\nHUAWEI_HEADLESS=false\n", encoding="utf-8")
    settings = load_settings(path)

    assert settings.log_level == "DEBUG"
    assert settings.huawei.headless is False


def test_env_file_rejects_malformed_lines(tmp_path):
    path = tmp_path / ".env"
    path.write_text("this is not a setting\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="expected KEY=value"):
        load_settings(path)


def test_selectors_are_collected(tmp_path):
    settings = load_settings(_write(tmp_path, "HUAWEI_SEL_NAV_AP=a.nav-ap\n"))

    assert settings.huawei.selectors["nav_ap"] == "a.nav-ap"


def test_settings_defaults_are_offline_safe(tmp_path):
    settings = load_settings(_write(tmp_path))

    assert settings.retry.attempts >= 1
    assert settings.report_month is None
    assert isinstance(settings, Settings)