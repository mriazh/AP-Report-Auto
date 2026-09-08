"""WhatsApp status notices through a GOWA-compatible gateway.

The request convention is the public, non-secret part of the pattern already in
use elsewhere in the office tooling: ``POST {base}/send/message`` with a JSON
body of ``{"phone": ..., "message": ...}`` and an optional ``X-Device-Id``
header. Gateway URL, device id and group JID all come from ``config/.env``.

Notification failures are logged and swallowed: a broken gateway must not turn a
successful export into a failed run, and must not hide the real error.
"""

from __future__ import annotations

import logging

from .settings import NotifySettings

logger = logging.getLogger(__name__)

SEND_PATH = "/send/message"
class Notifier:
    """Interface the pipeline depends on; tests substitute a recorder."""

    def send(self, message: str) -> bool:
        raise NotImplementedError
class NullNotifier(Notifier):
    """Used when notifications are switched off."""

    def send(self, message: str) -> bool:
        logger.info("notifications disabled; would have sent: %s", message)
        return False
class GowaNotifier(Notifier):
    """GOWA-compatible HTTP adapter.

    ``tls_verify`` is not negotiable here: verification is on unless the caller
    passes an explicit CA bundle, because the office portal uses an internal
    certificate.
    """

    def __init__(
        self,
        settings: NotifySettings,
        *,
        session=None,
        tls_verify: bool = True,
        ca_bundle: str = "",
    ) -> None:
        self.settings = settings
        self._session = session
        self.tls_verify = tls_verify
        self.ca_bundle = ca_bundle

    def _client(self):
        if self._session is not None:
            return self._session
        import requests

        return requests.Session()

    def send(self, message: str) -> bool:
        if not self.settings.enabled:
            logger.info("notifications disabled; skipping: %s", message)
            return False
        if not self.settings.base_url or not self.settings.group_jid:
            logger.warning("notifications enabled but GOWA_BASE_URL/GOWA_GROUP_JID unset; skipping")
            return False

        url = f"{self.settings.base_url.rstrip('/')}{SEND_PATH}"
        payload = {"phone": self.settings.group_jid, "message": message}
        headers = {"Content-Type": "application/json"}
        if self.settings.device_id:
            headers["X-Device-Id"] = self.settings.device_id

        try:
            client = self._client()
            verify = self.ca_bundle or self.tls_verify
            response = client.post(url, json=payload, headers=headers, timeout=self.settings.timeout_seconds, verify=verify)
        except Exception as exc:  # network/TLS/timeout
            # Never include the payload: it carries the group JID.
            logger.warning("notification failed (%s): %s", type(exc).__name__, exc)
            return False

        status = getattr(response, "status_code", 0)
        if status >= 400:
            logger.warning("notification rejected with HTTP %s", status)
            return False
        logger.info("notification delivered")
        return True
# --------------------------------------------------------------------------
# Message rendering
# --------------------------------------------------------------------------
def format_duration(seconds: float | int) -> str:
    """Format duration in seconds to human readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted duration string: <60s -> '{s}s', <3600s -> '{m}m {s}s', >=3600s -> '{h}h {m}m {s}s'
    """
    total_s = max(0, int(round(seconds)))
    if total_s < 60:
        return f"{total_s}s"
    elif total_s < 3600:
        m = total_s // 60
        s = total_s % 60
        return f"{m}m {s}s"
    else:
        h = total_s // 3600
        m = (total_s % 3600) // 60
        s = total_s % 60
        # Remove zero minutes and seconds for cleaner output
        if m == 0 and s == 0:
            return f"{h}h"
        elif s == 0:
            return f"{h}h {m}m"
        else:
            return f"{h}h {m}m {s}s"
def started_message(day, month: tuple[int, int], *, mode: str = "daily") -> str:
    return f"[Huawei AP Report Automation] START | mode={mode} | date={day.isoformat()}"
def success_message(day, month: tuple[int, int], report_names: list[str], *, elapsed: float | int = 0, reports: int = 3, records: int = 0, mode: str = "daily") -> str:
    return f"[Huawei AP Report Automation] SUCCESS | mode={mode} | date={day.isoformat()} | elapsed={format_duration(elapsed)} | reports={reports} | records={records}"
def failure_message(day, reason: str, *, elapsed: float | int = 0, attempts: int | None = None, mode: str = "daily") -> str:
    error_part = f"{reason} (after {attempts} attempts)" if attempts else reason
    return f"[Huawei AP Report Automation] FAILED | mode={mode} | date={day.isoformat()} | elapsed={format_duration(elapsed)} | error={error_part}"