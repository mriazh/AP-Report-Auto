"""Error classes.

Each failure mode the operator can act on gets its own class so the pipeline can
report *what* went wrong without leaking credentials, and so tests can assert on
the class instead of on message text.
"""

from __future__ import annotations


class ApReportError(Exception):
    """Base class for every error this package raises on purpose."""


class ConfigError(ApReportError):
    """`config/.env` is missing, unreadable, or has invalid values."""


class TemplateError(ApReportError):
    """A report template is missing or does not have the expected layout."""


class ExportValidationError(ApReportError):
    """A downloaded CSV is empty, truncated, or missing expected columns."""


class CollectionError(ApReportError):
    """The Huawei portal could not be reached, authenticated, or exported.

    ``attempts`` records how many bounded attempts were made.
    """

    def __init__(self, message: str, *, attempts: int | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts


class AuthError(CollectionError):
    """The portal rejected the configured credentials."""


class PortalUnavailableError(CollectionError):
    """The portal could not be reached (DNS, TCP, TLS, HTTP)."""


class ReportError(ApReportError):
    """A monthly workbook could not be generated; previous reports are intact."""


class NotificationError(ApReportError):
    """A GOWA notification could not be delivered.

    Never fatal: the collection/report result stays authoritative.
    """