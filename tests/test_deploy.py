"""Deployment artefacts: the systemd units and the documented setup steps.

These tests read the unit files as text rather than executing ``systemd-analyze``
because the target is a Debian host, not this machine. They check the schedule
and the security-relevant properties that a reviewer would otherwise have to
verify by hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy" / "systemd"
SERVICE = DEPLOY / "ap-report-auto.service"
TIMER = DEPLOY / "ap-report-auto.timer"


def _sections(text: str) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            sections[line[1:-1]] = {}
            continue
        key, _, value = line.partition("=")
        if _ and sections:
            sections[next(reversed(sections))][key.strip()] = value.strip()
    return sections


def _read(path: Path) -> str:
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def test_timer_fires_at_10_00_wib():
    timer = _sections(_read(TIMER))

    assert timer["Timer"]["OnCalendar"] == "*-*-* 10:00:00"
    assert timer["Timer"]["Timezone"] == "Asia/Jakarta"
    assert timer["Timer"]["Unit"] == "ap-report-auto.service"


def test_timer_persists_a_missed_run_to_the_next_boot():
    timer = _sections(_read(TIMER))

    # Persistent=true is what gives the single next-boot run; the requirements
    # ask for no catch-up beyond that.
    assert timer["Timer"]["Persistent"] == "true"
    assert "RandomizedDelaySec" not in timer["Timer"]


def test_timer_is_enabled_for_timers_target():
    timer = _sections(_read(TIMER))

    assert timer["Install"]["WantedBy"] == "timers.target"


def test_service_is_a_oneshot_that_invokes_the_cli():
    service = _sections(_read(SERVICE))

    assert service["Service"]["Type"] == "oneshot"
    assert service["Service"]["ExecStart"].endswith("ap-report-auto run")
    assert service["Service"]["WorkingDirectory"] == "/home/mriazh/Github-PC/AP-Report-Auto"
    assert service["Service"]["Environment"] == "TZ=Asia/Jakarta"


def test_service_does_not_store_secrets():
    text = _read(SERVICE)

    for marker in ("HUAWEI_PASSWORD", "GOWA_DEVICE_ID", "GOWA_GROUP_JID", "EnvironmentFile"):
        assert marker not in text, f"{marker} must not appear in the unit"


def test_service_runs_as_an_unprivileged_user():
    service = _sections(_read(SERVICE))

    assert service["Service"]["User"] == "mriazh"
    assert service["Service"]["Group"] == "mriazh"
    assert service["Service"]["UMask"] == "0077"


def test_service_waits_for_the_network_without_retrying_forever():
    service = _sections(_read(SERVICE))

    assert service["Unit"]["After"] == "network-online.target"
    assert service["Unit"]["Wants"] == "network-online.target"
    assert service["Service"]["Restart"] == "no"
    assert int(service["Service"]["TimeoutStartSec"].removesuffix("s")) <= 3600


def test_readme_documents_the_debian_install():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

    assert "systemctl" in readme
    assert "Asia/Jakarta" in readme
    assert "config/.env" in readme


@pytest.mark.parametrize("path", [SERVICE, TIMER], ids=lambda path: path.name)
def test_unit_files_have_the_required_sections(path):
    sections = _sections(_read(path))

    assert "Unit" in sections
    assert set(sections["Unit"]) & {"Description"}
    assert "Description" in sections["Unit"]