"""Portal adapter and retry behaviour, offline.

The browser, context and page below are fakes. They record what the collector
asked for — which context, which login, which view and export control, when the
session closed — so the daily session lifecycle is asserted without a portal, a
real download or a credential.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fixtures import ap_csv, ssid_csv
from huawei_ap_report.errors import AuthError, CollectionError
from huawei_ap_report.huawei import (
    CollectingExporter,
    Download,
    PlaywrightCollector,
    PortalExporter,
    validate_download,
)
from huawei_ap_report.settings import HuaweiSettings

LOGIN_URL = "https://172.16.24.3/view/login.html"
DASHBOARD_URL = "https://172.16.24.3/view/main/default.html"


class FakeExporter(PortalExporter):
    """Scripted exporter: records calls and replays canned results."""

    def __init__(self, results):
        self.results = list(results)
        self.calls: list[str] = []

    def export(self, filename: str) -> Download:
        self.calls.append(filename)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _collector(results, attempts=3, delays=None):
    slept: list[float] = []
    exporter = FakeExporter(results)
    return (
        CollectingExporter(
            exporter,
            attempts=attempts,
            delay_for=lambda attempt: 5 * attempt,
            sleep=slept.append,
        ),
        exporter,
        slept,
    )


def test_collect_returns_the_download_on_first_attempt():
    collector, exporter, slept = _collector([Download("apInfo.csv", ap_csv())])

    download = collector.collect("apInfo.csv")

    assert download.filename == "apInfo.csv"
    assert exporter.calls == ["apInfo.csv"]
    assert slept == []


def test_collect_retries_then_succeeds_with_backoff():
    collector, exporter, slept = _collector(
        [CollectionError("timeout"), CollectionError("timeout"), Download("apInfo.csv", ap_csv())]
    )

    download = collector.collect("apInfo.csv")

    assert download.filename == "apInfo.csv"
    assert exporter.calls == ["apInfo.csv"] * 3
    assert slept == [5, 10]


def test_collect_gives_up_after_bounded_attempts():
    collector, exporter, slept = _collector([CollectionError("boom")] * 3, attempts=3)

    with pytest.raises(CollectionError, match="failed after 3 attempts"):
        collector.collect("apInfo.csv")

    assert len(exporter.calls) == 3
    assert len(slept) == 2, "no sleep after the final attempt"


def test_collect_never_loops_forever():
    collector, exporter, _ = _collector([CollectionError("boom")] * 50, attempts=4)

    with pytest.raises(CollectionError):
        collector.collect("apInfo.csv")

    assert len(exporter.calls) == 4


def test_validate_download_rejects_an_empty_export():
    with pytest.raises(Exception, match="empty"):
        validate_download("apInfo.csv", b"\xef\xbb\xbf")


def test_validate_download_rejects_a_truncated_export():
    truncated = ap_csv().decode("utf-8-sig").splitlines()[0]

    with pytest.raises(Exception, match="no data rows"):
        validate_download("apInfo.csv", truncated.encode("utf-8"))


def test_validate_download_accepts_both_real_shapes():
    assert validate_download("apInfo.csv", ap_csv()).filename == "apInfo.csv"
    assert validate_download("ssidInfo.csv", ssid_csv()).filename == "ssidInfo.csv"


# --------------------------------------------------------------------------
# Selector values observed in the operator's saved portal pages. Static DOM
# evidence only: no live run, no validated download, no credential.
# --------------------------------------------------------------------------

LOGIN_SELECTORS = {
    "login_username": "#username",
    "login_password": "#password",
    "login_submit": "#submitButton",
}
AP_SELECTORS = {"nav_ap": "#dashAP", "export_ap": "#outputApListInfoBtnEn"}
SSID_SELECTORS = {"nav_ssid": "#dashSSIDs", "export_ssid": "#ssidListExportBtn"}
EXPORT_CONTROLS = (AP_SELECTORS["export_ap"], SSID_SELECTORS["export_ssid"])


class FakeDownload:
    """What ``page.expect_download()`` yields: a handle with ``save_as``."""

    def __init__(self, payload: bytes, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error

    def save_as(self, target: str) -> None:
        """Real Playwright ``Download.save_as`` returns None, not the path."""

        if self.error is not None:
            raise self.error
        Path(target).write_bytes(self.payload)


class FakeDownloadContext:
    """The context manager ``expect_download()`` returns."""

    def __init__(self, page: "FakePage", timeout=None) -> None:
        self.page = page
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    @property
    def value(self) -> FakeDownload:
        return self.page.download


class FakePage:
    """One page: records logins/nav/export clicks and serves scripted downloads."""

    def __init__(
        self,
        *,
        payloads: dict[str, bytes] | None = None,
        download_errors: list[Exception] | None = None,
        expires_after_exports: int | None = None,
        rejects_login: bool = False,
    ) -> None:
        self.url = ""
        self.filled: list[tuple[str, str]] = []
        self.clicks: list[str] = []
        self.visited: list[str] = []
        self.download = FakeDownload(b"")
        self.closed = False
        #: Payload served for each export control.
        self.payloads = payloads or {
            AP_SELECTORS["export_ap"]: ap_csv(),
            SSID_SELECTORS["export_ssid"]: ssid_csv(),
        }
        #: Export failures to raise, one per attempt, then normal service.
        self.download_errors = list(download_errors or [])
        #: Exports after which the portal drops the session back to the login page.
        self.expires_after_exports = expires_after_exports
        self.rejects_login = rejects_login
        self.export_clicks = 0

    # -- Playwright surface the collector uses -----------------------------

    def goto(self, url, **kwargs):
        self.visited.append(url)
        self.url = url

    def fill(self, selector, value, **kwargs):
        self.filled.append((selector, value))

    def click(self, selector, **kwargs):
        self.clicks.append(selector)
        if selector == LOGIN_SELECTORS["login_submit"]:
            self.url = LOGIN_URL if self.rejects_login else DASHBOARD_URL
            return
        if selector not in EXPORT_CONTROLS:
            return
        self.export_clicks += 1
        error = self.download_errors.pop(0) if self.download_errors else None
        self.download = FakeDownload(self.payloads[selector], error)
        if self.expires_after_exports is not None and self.export_clicks >= self.expires_after_exports:
            self.url = LOGIN_URL

    def wait_for_load_state(self, *args, **kwargs):
        return None

    def expect_download(self, timeout=None):
        return FakeDownloadContext(self, timeout)

    def close(self):
        self.closed = True

    # -- assertions helpers -----------------------------------------------

    @property
    def logins(self) -> int:
        return self.clicks.count(LOGIN_SELECTORS["login_submit"])

    @property
    def typed_passwords(self) -> list[str]:
        return [value for selector, value in self.filled if selector == LOGIN_SELECTORS["login_password"]]


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.contexts: list[FakeContext] = []
        #: kwargs each ``new_context()`` call was given, in order.
        self.context_kwargs: list[dict[str, object]] = []
        self.closed = False

    def new_context(self, **kwargs):
        context = FakeContext(self.page)
        self.contexts.append(context)
        self.context_kwargs.append(kwargs)
        return context

    def close(self):
        self.closed = True


def _settings(**overrides) -> HuaweiSettings:
    base = dict(
        base_url="https://172.16.24.3",
        login_path="/view/login.html",
        username="operator",
        password="secret",
        headless=True,
        login_timeout_seconds=5,
        export_timeout_seconds=5,
        nav_timeout_seconds=5,
        ap_filename="apInfo.csv",
        ssid_filename="ssidInfo.csv",
        selectors={**LOGIN_SELECTORS, **AP_SELECTORS, **SSID_SELECTORS},
    )
    base.update(overrides)
    return HuaweiSettings(**base)


def _portal_collector(page: FakePage, tmp_path: Path, **overrides) -> PlaywrightCollector:
    """A collector wired to a fake browser, staging downloads in ``tmp_path``."""

    return PlaywrightCollector(
        _settings(**overrides),
        browser=FakeBrowser(page),
        download_dir=tmp_path / "downloads",
    )


def test_collector_refuses_to_start_without_selectors():
    collector = PlaywrightCollector(_settings(selectors={"login_username": "#user"}))

    with pytest.raises(CollectionError, match="HUAWEI_SEL_"):
        collector.export("apInfo.csv")


def test_collector_refuses_the_ssid_export_without_its_own_control(tmp_path):
    """AP's export control is not allowed to stand in for the SSID one."""

    page = FakePage()
    collector = _portal_collector(page, tmp_path, selectors={**LOGIN_SELECTORS, **AP_SELECTORS})

    collector.export("apInfo.csv")

    with pytest.raises(CollectionError, match="export_ssid"):
        collector.export("ssidInfo.csv")


def test_collector_wraps_playwright_errors():
    class BrokenBrowser:
        def new_context(self, **kwargs):
            raise RuntimeError("browser exploded")

    collector = PlaywrightCollector(_settings(), browser=BrokenBrowser())

    with pytest.raises(CollectionError, match="cannot start a browser"):
        collector.export("apInfo.csv")


def test_both_exports_share_one_context_and_one_login(tmp_path):
    """The daily session is opened once: one context, one page, one login."""

    page = FakePage()
    browser = FakeBrowser(page)
    collector = PlaywrightCollector(
        _settings(),
        browser=browser,
        download_dir=tmp_path / "downloads",
    )

    collector.export("apInfo.csv")
    collector.export("ssidInfo.csv")
    collector.close()

    assert len(browser.contexts) == 1
    assert page.logins == 1
    assert page.visited == [LOGIN_URL]
    assert page.closed is True
    assert browser.contexts[0].closed is True
    assert browser.closed is True


def test_each_filename_uses_its_own_view_and_export_control(tmp_path):
    page = FakePage()
    browser = FakeBrowser(page)
    collector = PlaywrightCollector(
        _settings(),
        browser=browser,
        download_dir=tmp_path / "downloads",
    )

    collector.export("apInfo.csv")
    ap_clicks = list(page.clicks)
    page.clicks.clear()

    collector.export("ssidInfo.csv")
    ssid_clicks = list(page.clicks)

    assert ap_clicks[-2:] == ["#dashAP", "#outputApListInfoBtnEn"]
    assert ssid_clicks == ["#dashSSIDs", "#ssidListExportBtn"]
    collector.close()


def test_exports_are_staged_under_their_requested_filenames(tmp_path):
    page = FakePage()
    collector = _portal_collector(page, tmp_path)

    collector.export("apInfo.csv")
    collector.export("ssidInfo.csv")

    staged = sorted(path.name for path in (tmp_path / "downloads").iterdir())
    assert staged == ["apInfo.csv", "ssidInfo.csv"]
    assert (tmp_path / "downloads" / "apInfo.csv").read_bytes() == ap_csv()


def test_an_unknown_filename_is_rejected_rather_than_guessed(tmp_path):
    collector = _portal_collector(FakePage(), tmp_path)

    with pytest.raises(CollectionError, match="unknown export filename"):
        collector.export("somethingElse.csv")


def test_ordinary_download_failure_is_retried_on_the_same_session(tmp_path):
    """A transient export failure must not spend a second login."""

    page = FakePage(download_errors=[RuntimeError("export control not ready")])
    collector = _portal_collector(page, tmp_path)
    retries = CollectingExporter(
        collector,
        attempts=3,
        delay_for=lambda attempt: 0,
        sleep=lambda _: None,
    )

    download = retries.collect("apInfo.csv")

    assert download.filename == "apInfo.csv"
    assert page.logins == 1, "the retry reuses the authenticated session"
    assert page.visited == [LOGIN_URL], "the login page is loaded once"
    assert page.export_clicks == 2
    collector.close()


def test_an_expired_session_reauthenticates_once_and_continues(tmp_path):
    """A session dropped back to the login page gets one new login, not a new context."""

    page = FakePage(expires_after_exports=1)
    collector = _portal_collector(page, tmp_path)

    assert collector.export("apInfo.csv").filename == "apInfo.csv"
    assert page.url == LOGIN_URL, "the AP export left the session expired"

    assert collector.export("ssidInfo.csv").filename == "ssidInfo.csv"
    assert page.logins == 2
    assert len(collector.browser.contexts) == 1
    collector.close()


def test_reauthentication_is_bounded_to_one_per_session(tmp_path):
    """A portal that expires again immediately fails the run instead of looping."""

    page = FakePage(expires_after_exports=1)
    collector = _portal_collector(page, tmp_path)
    collector.export("apInfo.csv")
    collector.export("ssidInfo.csv")
    logins_before = page.logins

    with pytest.raises(AuthError, match="expired again"):
        collector.export("apInfo.csv")

    assert page.logins == logins_before, "no additional login is attempted"
    collector.close()


def test_a_rejected_login_is_an_auth_error(tmp_path):
    page = FakePage(rejects_login=True)
    collector = _portal_collector(page, tmp_path)

    with pytest.raises(AuthError, match="rejected"):
        collector.export("apInfo.csv")

    assert page.logins == 1, "credentials are not retyped in a loop"
    collector.close()


def test_credentials_are_typed_into_the_form_and_never_logged(tmp_path, caplog):
    page = FakePage()
    collector = _portal_collector(page, tmp_path)

    collector.export("apInfo.csv")

    assert (LOGIN_SELECTORS["login_username"], "operator") in page.filled
    assert (LOGIN_SELECTORS["login_password"], "secret") in page.filled
    assert "secret" not in caplog.text


def test_close_is_idempotent_and_safe_after_a_failed_export(tmp_path):
    page = FakePage(download_errors=[RuntimeError("click timed out")] * 5)
    collector = _portal_collector(page, tmp_path)

    with pytest.raises(CollectionError, match="export of apInfo.csv"):
        collector.export("apInfo.csv")

    collector.close()
    collector.close()

    assert page.closed is True
    assert collector.browser is None


def test_the_context_manager_closes_the_session_when_the_body_raises(tmp_path):
    page = FakePage()
    collector = _portal_collector(page, tmp_path)

    with pytest.raises(RuntimeError, match="report generation exploded"):
        with collector:
            collector.export("apInfo.csv")
            raise RuntimeError("report generation exploded")

    assert page.closed is True
    assert collector.browser is None


def test_collector_launches_without_disabling_tls_verification():
    """The browser is launched with verification intact.

    A regression here would mean shipping ``ignore_https_errors=True``, which the
    requirements forbid, so the launch kwargs are asserted rather than assumed.
    """

    launched: dict[str, object] = {}

    class FakeChromium:
        def launch(self, **kwargs):
            launched.update(kwargs)
            raise RuntimeError("stop after recording")

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeStarter:
        chromium = FakeChromium()

        def __init__(self, **kwargs):
            launched.update(kwargs)

    class SyncPlaywright:
        chromium = FakeChromium()

        def start(self):
            return FakeStarter()

    import sys
    import types

    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = SyncPlaywright
    sys.modules["playwright"] = types.ModuleType("playwright")
    sys.modules["playwright.sync_api"] = module
    try:
        with pytest.raises(CollectionError, match="cannot start a browser"):
            PlaywrightCollector(_settings(headless=True)).export("apInfo.csv")
    finally:
        for name in ("playwright.sync_api", "playwright"):
            sys.modules.pop(name, None)

    assert launched.get("headless") is True
    assert "ignore_https_errors" not in launched


def test_context_keeps_certificate_verification_on_by_default(tmp_path):
    """``TLS_VERIFY=true`` must reach the browser context as verification on."""

    page = FakePage()
    browser = FakeBrowser(page)
    collector = PlaywrightCollector(_settings(), browser=browser, download_dir=tmp_path)

    collector.export("apInfo.csv")

    assert browser.context_kwargs[0]["ignore_https_errors"] is False
    collector.close()


def test_explicit_tls_opt_out_is_scoped_to_the_context(tmp_path):
    """``TLS_VERIFY=false`` relaxes the context for a self-signed portal cert."""

    page = FakePage()
    browser = FakeBrowser(page)
    collector = PlaywrightCollector(
        _settings(), browser=browser, download_dir=tmp_path, tls_verify=False
    )

    collector.export("apInfo.csv")

    assert browser.context_kwargs[0]["ignore_https_errors"] is True
    collector.close()


def test_login_failure_raises_auth_error():
    class FakePage_:
        url = LOGIN_URL

        def goto(self, *args, **kwargs):
            return None

        def fill(self, *args, **kwargs):
            return None

        def click(self, *args, **kwargs):
            return None

        def wait_for_load_state(self, *args, **kwargs):
            return None

    collector = PlaywrightCollector(_settings())
    with pytest.raises(CollectionError, match="rejected|failed"):
        collector._login(FakePage_())
