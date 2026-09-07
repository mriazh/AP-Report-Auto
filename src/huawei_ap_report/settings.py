"""Configuration from ``config/.env``.

Values come from the process environment first (so systemd can inject them) and
fall back to the local ``.env`` file. Nothing here logs or echoes a value that
could be a secret, and no default ever disables TLS verification.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .errors import CollectionError, ConfigError

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Asia/Jakarta"
#: WIB has no DST; the fixed offset is the fallback when tzdata is unavailable.
_WIB = timezone(timedelta(hours=7), name="WIB")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=value`` env file. Missing file is an error."""

    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"configuration file not found: {path} (copy config/.env.example to config/.env)")
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        if not separator:
            raise ConfigError(f"{path}:{number}: expected KEY=value")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _bool(values: dict[str, str], key: str, default: bool) -> bool:
    raw = values.get(key)
    if raw is None or raw == "":
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigError(f"{key} must be true/false, got {raw!r}")


def _int(values: dict[str, str], key: str, default: int, *, minimum: int = 0) -> int:
    raw = values.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}, got {value}")
    return value


def _float(values: dict[str, str], key: str, default: float, *, minimum: float = 0.0) -> float:
    raw = values.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}, got {value}")
    return value


def local_timezone(name: str = DEFAULT_TIMEZONE):
    """Return a tzinfo for ``name``, falling back to WIB without tzdata."""

    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        if name == DEFAULT_TIMEZONE:
            logger.warning("tzdata has no %s; using the fixed WIB offset (+07:00)", name)
            return _WIB
        raise ConfigError(f"unknown timezone {name!r}") from exc


#: ``HUAWEI_SEL_*`` name -> env key, so a configuration error names the exact
#: setting to fix. These are CSS/Playwright locators for portal elements; they
#: are not credentials.
SELECTOR_ENV_KEYS = {
    "login_username": "HUAWEI_SEL_LOGIN_USERNAME",
    "login_password": "HUAWEI_SEL_LOGIN_PASSWORD",
    "login_submit": "HUAWEI_SEL_LOGIN_SUBMIT",
    "nav_ap": "HUAWEI_SEL_NAV_AP",
    "nav_ssid": "HUAWEI_SEL_NAV_SSID",
    "export_ap": "HUAWEI_SEL_EXPORT_AP",
    "export_ssid": "HUAWEI_SEL_EXPORT_SSID",
}

#: Selectors every authenticated session needs, whatever view is exported.
LOGIN_SELECTOR_KEYS = ("login_username", "login_password", "login_submit")


@dataclass(frozen=True)
class PortalView:
    """One dashboard view: the sidebar entry that opens it and its export control.

    The AP and SSID dashboards export through different controls, so each view
    carries its own pair rather than sharing one export button.
    """

    name: str
    filename: str
    nav_key: str
    export_key: str
    nav_selector: str
    export_selector: str

    @property
    def selector_keys(self) -> tuple[str, ...]:
        return (self.nav_key, self.export_key)


@dataclass(frozen=True)
class HuaweiSettings:
    base_url: str
    login_path: str
    username: str
    password: str
    headless: bool
    login_timeout_seconds: float
    export_timeout_seconds: float
    nav_timeout_seconds: float
    ap_filename: str
    ssid_filename: str
    selectors: dict[str, str] = field(default_factory=dict)

    @property
    def login_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.login_path}"

    def view_for(self, filename: str) -> PortalView:
        """Return the view that produces ``filename``.

        An unknown name is an error rather than a guess: silently exporting the
        SSID view for an AP filename would publish the wrong file.
        """

        if filename == self.ap_filename:
            name, nav_key, export_key = "AP", "nav_ap", "export_ap"
        elif filename == self.ssid_filename:
            name, nav_key, export_key = "SSID", "nav_ssid", "export_ssid"
        else:
            raise CollectionError(
                f"unknown export filename {filename!r}: the portal session is configured for "
                f"{self.ap_filename!r} and {self.ssid_filename!r}"
            )
        return PortalView(
            name=name,
            filename=filename,
            nav_key=nav_key,
            export_key=export_key,
            nav_selector=self.selectors.get(nav_key, ""),
            export_selector=self.selectors.get(export_key, ""),
        )


@dataclass(frozen=True)
class RetrySettings:
    attempts: int
    backoff_seconds: float
    max_backoff_seconds: float

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff, capped: attempt is 1-based."""

        return min(self.backoff_seconds * (2 ** max(0, attempt - 1)), self.max_backoff_seconds)


@dataclass(frozen=True)
class NotifySettings:
    enabled: bool
    base_url: str
    device_id: str
    group_jid: str
    timeout_seconds: float


@dataclass(frozen=True)
class Settings:
    huawei: HuaweiSettings
    retry: RetrySettings
    notify: NotifySettings
    template_dir: Path
    output_root: Path
    report_month: tuple[int, int] | None
    timezone_name: str
    tls_verify: bool
    tls_ca_bundle: str
    log_level: str

    @property
    def tzinfo(self):
        return local_timezone(self.timezone_name)

    def now(self) -> datetime:
        return datetime.now(self.tzinfo)

    def month_of(self, moment: datetime | None = None) -> tuple[int, int]:
        moment = moment or self.now()
        return (moment.year, moment.month)

    def verify_configured(self) -> None:
        """Raise unless the values a live run needs are present.

        Placeholders are rejected so a half-filled ``.env`` fails before the
        browser starts instead of at the login form.
        """

        missing = [
            key
            for key, value in (("HUAWEI_USERNAME", self.huawei.username), ("HUAWEI_PASSWORD", self.huawei.password))
            if not value or value == "CHANGE_ME"
        ]
        if missing:
            raise ConfigError(f"set {', '.join(missing)} in config/.env before running against the portal")
        if self.huawei.ssid_filename == self.huawei.ap_filename:
            raise ConfigError("AP_EXPORT_FILENAME and SSID_EXPORT_FILENAME must differ")


def load_settings(env_file: Path | str | None = None, *, environ: dict[str, str] | None = None) -> Settings:
    """Load settings from ``environ`` layered over the ``.env`` file."""

    source = os.environ if environ is None else environ
    env_path = Path(env_file) if env_file is not None else Path("config/.env")
    values = _read_env_file(env_path)
    # Process environment wins, so systemd can override a value without editing
    # the file on disk.
    values.update({key: value for key, value in source.items() if key in _KNOWN_KEYS})

    if values.get("HUAWEI_SEL_EXPORT_BUTTON"):
        logger.warning(
            "HUAWEI_SEL_EXPORT_BUTTON is no longer used: the AP and SSID views export "
            "through different controls, set HUAWEI_SEL_EXPORT_AP and HUAWEI_SEL_EXPORT_SSID"
        )

    selectors = {key: values.get(env_key, "") for key, env_key in SELECTOR_ENV_KEYS.items()}

    huawei = HuaweiSettings(
        base_url=values.get("HUAWEI_BASE_URL", "https://172.16.24.3"),
        login_path=values.get("HUAWEI_LOGIN_PATH", "/view/login.html"),
        username=values.get("HUAWEI_USERNAME", ""),
        password=values.get("HUAWEI_PASSWORD", ""),
        headless=_bool(values, "HUAWEI_HEADLESS", True),
        login_timeout_seconds=_float(values, "HUAWEI_LOGIN_TIMEOUT_SECONDS", 45.0, minimum=1),
        export_timeout_seconds=_float(values, "HUAWEI_EXPORT_TIMEOUT_SECONDS", 180.0, minimum=1),
        nav_timeout_seconds=_float(values, "HUAWEI_NAV_TIMEOUT_SECONDS", 60.0, minimum=1),
        ap_filename=values.get("AP_EXPORT_FILENAME", "apInfo.csv"),
        ssid_filename=values.get("SSID_EXPORT_FILENAME", "ssidInfo.csv"),
        selectors=selectors,
    )
    retry = RetrySettings(
        attempts=_int(values, "RETRY_ATTEMPTS", 5, minimum=1),
        backoff_seconds=_float(values, "RETRY_BACKOFF_SECONDS", 30.0),
        max_backoff_seconds=_float(values, "RETRY_MAX_BACKOFF_SECONDS", 300.0),
    )
    notify = NotifySettings(
        enabled=_bool(values, "GOWA_ENABLED", True),
        base_url=values.get("GOWA_BASE_URL", ""),
        device_id=values.get("GOWA_DEVICE_ID", ""),
        group_jid=values.get("GOWA_GROUP_JID", ""),
        timeout_seconds=_float(values, "GOWA_TIMEOUT_SECONDS", 20.0, minimum=1),
    )
    report_month = _parse_month(values.get("REPORT_MONTH", "").strip())
    return Settings(
        huawei=huawei,
        retry=retry,
        notify=notify,
        template_dir=Path(values.get("TEMPLATE_DIR", "config/templates")),
        output_root=Path(values.get("OUTPUT_ROOT", "output")),
        report_month=report_month,
        timezone_name=values.get("TIMEZONE", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE,
        tls_verify=_bool(values, "TLS_VERIFY", True),
        tls_ca_bundle=values.get("TLS_CA_BUNDLE", ""),
        log_level=values.get("LOG_LEVEL", "INFO").upper(),
    )


def _parse_month(raw: str) -> tuple[int, int] | None:
    if not raw:
        return None
    for pattern in ("%Y-%m", "%Y_%m", "%Y/%m"):
        try:
            parsed = datetime.strptime(raw, pattern)
        except ValueError:
            continue
        return (parsed.year, parsed.month)
    raise ConfigError(f"REPORT_MONTH must look like YYYY-MM, got {raw!r}")


_KNOWN_KEYS = frozenset(
    {
        "HUAWEI_BASE_URL",
        "HUAWEI_LOGIN_PATH",
        "HUAWEI_USERNAME",
        "HUAWEI_PASSWORD",
        "HUAWEI_HEADLESS",
        "HUAWEI_LOGIN_TIMEOUT_SECONDS",
        "HUAWEI_EXPORT_TIMEOUT_SECONDS",
        "HUAWEI_NAV_TIMEOUT_SECONDS",
        "HUAWEI_SEL_LOGIN_USERNAME",
        "HUAWEI_SEL_LOGIN_PASSWORD",
        "HUAWEI_SEL_LOGIN_SUBMIT",
        "HUAWEI_SEL_NAV_AP",
        "HUAWEI_SEL_NAV_SSID",
        # Read once for a migration warning; the two per-view controls are used.
        "HUAWEI_SEL_EXPORT_BUTTON",
        "HUAWEI_SEL_EXPORT_AP",
        "HUAWEI_SEL_EXPORT_SSID",
        "AP_EXPORT_FILENAME",
        "SSID_EXPORT_FILENAME",
        "RETRY_ATTEMPTS",
        "RETRY_BACKOFF_SECONDS",
        "RETRY_MAX_BACKOFF_SECONDS",
        "TEMPLATE_DIR",
        "OUTPUT_ROOT",
        "REPORT_MONTH",
        "KEEP_EMPTY_DAY_TABS",
        "GOWA_ENABLED",
        "GOWA_BASE_URL",
        "GOWA_DEVICE_ID",
        "GOWA_GROUP_JID",
        "GOWA_TIMEOUT_SECONDS",
        "TLS_VERIFY",
        "TLS_CA_BUNDLE",
        "LOG_LEVEL",
        "TIMEZONE",
    }
)