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


def started_message(day, month: tuple[int, int]) -> str:
    return f"AP Report Auto: mulai pengambilan data {day.isoformat()} ( laporan {month[0]:04d}-{month[1]:02d} )."


def success_message(day, month: tuple[int, int], report_names: list[str]) -> str:
    listed = ", ".join(report_names)
    return (
        f"AP Report Auto: BERHASIL {day.isoformat()}. "
        f"Laporan {month[0]:04d}-{month[1]:02d} diperbarui ({listed})."
    )


def failure_message(day, reason: str, *, attempts: int | None = None) -> str:
    detail = f" AP Report Auto: GAGAL {day.isoformat()} setelah {attempts} percobaan." if attempts else f" AP Report Auto: GAGAL {day.isoformat()}."
    return f"{detail} Penyebab: {reason}. Laporan bulan ini tidak diubah."