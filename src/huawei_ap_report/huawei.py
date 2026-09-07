"""Huawei portal collection.

``PlaywrightCollector`` drives a real browser; ``PortalExporter`` is the seam the
pipeline depends on so the retry, validation and archive logic can be tested with
a stub. Downloads are captured from the browser's download event, validated, and
handed back as bytes — nothing is written to the archive by this module.

One daily job uses one authenticated browser context: login once, then export
both CSVs from the same page. The session is closed in a ``finally`` path. If
the session is recognised as expired (the page is back on the login form) the
collector may reauthenticate once for the current job; ordinary download
failures simply retry on the existing session.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import AuthError, CollectionError, PortalUnavailableError
from .exports import validate_named_export
from .settings import HuaweiSettings, PortalView, SELECTOR_ENV_KEYS, LOGIN_SELECTOR_KEYS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Download:
    """One exported CSV, already parsed and validated."""

    filename: str
    data: bytes

    def text(self) -> str:
        return self.data.decode("utf-8-sig")


class PortalExporter:
    """Interface: log in, then export one file by name."""

    def export(self, filename: str) -> Download:
        raise NotImplementedError


class PlaywrightCollector(PortalExporter):
    """Headless-browser adapter for the Huawei iMaster NCE dashboard.

    Selectors come from the configuration so a portal UI change is a config edit,
    not a code change. Credentials are typed through Playwright's ``fill`` and are
    never logged; cookies stay inside the browser context.

    One collector instance is one daily job: ``open()`` launches a single browser
    context and logs in once; every ``export()`` reuses that authenticated page;
    ``close()`` tears the session down. ``MAX_REAUTHENTICATIONS`` limits how
    many times a session that falls back to the login page may be re-logged in.
    """

    #: A job may reauthenticate once when the session is recognized as expired.
    MAX_REAUTHENTICATIONS = 1

    def __init__(
        self,
        settings: HuaweiSettings,
        *,
        browser=None,
        tls_ca_bundle: str = "",
        download_dir: str | Path | None = None,
        tls_verify: bool = True,
    ) -> None:
        self.settings = settings
        self._browser = browser
        self._playwright = None
        self._context = None
        self._page = None
        self._reauthentications = 0
        self.tls_ca_bundle = tls_ca_bundle
        #: Secure by default; only an explicit ``TLS_VERIFY=false`` in the env
        #: file relaxes certificate checking for the portal's own self-signed
        #: internal certificate. Lives on the context, never on the launch.
        self.tls_verify = tls_verify
        self.download_dir = Path(download_dir) if download_dir is not None else Path(".downloads")

    @property
    def browser(self):
        return self._browser

    # -- lifecycle ---------------------------------------------------------

    def _ensure_browser(self):
        if self._browser is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:  # pragma: no cover - depends on host
                raise CollectionError(
                    "playwright is not installed: pip install '.[browser]' && playwright install chromium"
                ) from exc
            self._playwright = sync_playwright().start()
            # The browser is launched with verification intact. Any relaxation is
            # scoped to the context (see ``tls_verify``) and only when the env
            # file asks for it explicitly.
            self._browser = self._launch()
        return self._browser

    def _launch(self):
        try:
            return self._playwright.chromium.launch(headless=self.settings.headless)
        except Exception as exc:
            raise PortalUnavailableError(f"cannot start a browser session: {exc}") from exc

    def open(self) -> None:
        """Start one authenticated session; safe to call more than once."""

        if self._page is not None:
            return
        self._require_selectors(*LOGIN_SELECTOR_KEYS)
        browser = self._ensure_browser()
        try:
            self._context = browser.new_context(
                accept_downloads=True, ignore_https_errors=not self.tls_verify
            )
            self._page = self._context.new_page()
        except Exception as exc:
            self.close()
            raise PortalUnavailableError(f"cannot start a browser session: {exc}") from exc
        try:
            self._login(self._page)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Tear the session down. Idempotent, and safe after a failed login."""

        if self._page is not None:
            _close_quietly(self._page, "page")
            self._page = None
        if self._context is not None:
            _close_quietly(self._context, "context")
            self._context = None
        if self._browser is not None:
            _close_quietly(self._browser, "browser")
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def __enter__(self) -> "PlaywrightCollector":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- export ------------------------------------------------------------

    def export(self, filename: str) -> Download:
        view = self.settings.view_for(filename)
        self._require_selectors(*LOGIN_SELECTOR_KEYS, *view.selector_keys)
        self.open()
        self._ensure_authenticated()
        self._open_view(view)
        return self._download(view)

    def _require_selectors(self, *keys: str) -> None:
        missing = [k for k in keys if not self.settings.selectors.get(k)]
        if missing:
            env = ", ".join(f"{SELECTOR_ENV_KEYS[k]} ({k})" for k in missing)
            raise CollectionError(
                f"the following portal selectors are not configured (set them in config/.env): {env}"
            )

    def _ensure_authenticated(self) -> None:
        """Re-login at most once per job when the session is recognized as expired.

        An ordinary download failure leaves the session in place: the caller's
        bounded retry reuses it. Only a login page is treated as proof that the
        session died, and only one reauthentication is allowed per job.
        """

        if not _still_on_login_page(self._page):
            return
        if self._reauthentications >= self.MAX_REAUTHENTICATIONS:
            raise AuthError(
                f"the portal session is expired again after {self._reauthentications} "
                f"reauthentication in this job"
            )
        self._reauthentications += 1
        logger.warning("portal session expired; reauthenticating (%d/%d)", self._reauthentications, self.MAX_REAUTHENTICATIONS)
        self._login(self._page)

    def _login(self, page) -> None:
        settings = self.settings
        selectors = settings.selectors
        try:
            page.goto(settings.login_url, timeout=settings.login_timeout_seconds * 1000)
            page.fill(selectors["login_username"], settings.username)
            page.fill(selectors["login_password"], settings.password)
            page.click(selectors["login_submit"])
            page.wait_for_load_state("networkidle", timeout=settings.login_timeout_seconds * 1000)
        except Exception as exc:
            raise AuthError(f"login to {settings.base_url} failed: {type(exc).__name__}") from exc
        if _still_on_login_page(page):
            raise AuthError("the portal rejected the configured credentials")

    def _open_view(self, view: PortalView) -> None:
        settings = self.settings
        try:
            self._page.click(view.nav_selector, timeout=settings.nav_timeout_seconds * 1000)
            self._page.wait_for_load_state("networkidle", timeout=settings.nav_timeout_seconds * 1000)
        except Exception as exc:
            raise CollectionError(f"cannot open the {view.name} view: {type(exc).__name__}") from exc

    def _download(self, view: PortalView) -> Download:
        settings = self.settings
        self.download_dir.mkdir(parents=True, exist_ok=True)
        try:
            with self._page.expect_download(timeout=settings.export_timeout_seconds * 1000) as info:
                self._page.click(view.export_selector)
            destination = self.download_dir / view.filename
            # save_as() returns None; the destination is the source of truth.
            info.value.save_as(str(destination))
        except Exception as exc:
            raise CollectionError(f"export of {view.filename} timed out or failed: {type(exc).__name__}") from exc
        data = destination.read_bytes()
        return validate_download(view.filename, data)


def _close_quietly(target, what: str) -> None:
    """Close a Playwright object without letting teardown mask the real error."""

    try:
        target.close()
    except Exception as exc:
        logger.warning("closing the browser %s failed: %s: %s", what, type(exc).__name__, exc)


def _still_on_login_page(page) -> bool:
    try:
        url = page.url or ""
    except Exception:  # pragma: no cover - defensive
        return False
    return "login" in url.lower()


def validate_download(filename: str, data: bytes) -> Download:
    """Reject an export that is empty, truncated, or missing expected columns.

    A failed run must never publish a partial pair, so validation happens the
    moment the bytes arrive rather than at archive time.
    """

    validate_named_export(filename, data)
    return Download(filename=filename, data=data)


class CollectingExporter:
    """Runs one export with bounded retries and exponential backoff.

    ``sleep`` and ``clock`` are injectable so tests can assert the retry count
    without waiting.
    """

    def __init__(
        self,
        exporter: PortalExporter,
        *,
        attempts: int,
        delay_for: Callable[[int], float],
        sleep: Callable[[float], None],
    ) -> None:
        self.exporter = exporter
        self.attempts = attempts
        self.delay_for = delay_for
        self.sleep = sleep

    def collect(self, filename: str) -> Download:
        last: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            try:
                download = self.exporter.export(filename)
                logger.info("exported %s on attempt %d", filename, attempt)
                return download
            except CollectionError as exc:
                last = exc
                logger.warning(
                    "export of %s failed on attempt %d/%d: %s", filename, attempt, self.attempts, exc
                )
                if attempt < self.attempts:
                    self.sleep(self.delay_for(attempt))
        raise CollectionError(f"{filename} failed after {self.attempts} attempts: {last}") from last