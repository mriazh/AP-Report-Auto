"""Daily job: collect → archive → rebuild the month → notify.

Failure policy, in the order the requirements set it:

* the run announces itself, then collects both CSVs with bounded retries;
* a partial or invalid pair is never archived, so a failed collection leaves the
  previous raw day and the previous reports untouched;
* the three reports are staged and published as a set;
* start/success/failure notices are attempted, and a broken gateway is logged
  rather than allowed to mask the real result.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .archive import RawSnapshot, publish_pair
from .errors import ApReportError, CollectionError, ReportError, TemplateError
from .huawei import CollectingExporter, PortalExporter
from .notifier import Notifier, failure_message, started_message, success_message
from .reports.publish import MonthPlan, rebuild_from_archive
from .settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    """Outcome of one daily run."""

    day: date
    month: tuple[int, int]
    ok: bool
    snapshot: RawSnapshot | None = None
    reports: list[Path] = field(default_factory=list)
    error: str = ""

    @property
    def report_names(self) -> list[str]:
        return [path.name for path in self.reports]


class DailyJob:
    """Wires the exporter, the archive, the reports and the notifier together."""

    def __init__(
        self,
        settings: Settings,
        exporter: PortalExporter,
        notifier: Notifier,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.collector = CollectingExporter(
            exporter,
            attempts=settings.retry.attempts,
            delay_for=settings.retry.delay_for,
            sleep=sleep,
        )
        self.notifier = notifier

    def run(self, day: date | None = None) -> RunResult:
        settings = self.settings
        day = day or settings.now().date()
        month = settings.report_month or (day.year, day.month)

        self._notify(started_message(day, month))

        try:
            try:
                snapshot = self.collect_and_archive(day)
            except (CollectionError, ApReportError) as exc:
                logger.error("collection failed for %s: %s", day.isoformat(), exc)
                attempts = settings.retry.attempts if isinstance(exc, CollectionError) else None
                self._notify(failure_message(day, str(exc), attempts=attempts))
                return RunResult(day=day, month=month, ok=False, error=str(exc))

            try:
                plan = MonthPlan(
                    year=month[0],
                    month=month[1],
                    template_dir=settings.template_dir,
                    output_root=settings.output_root,
                )
                outputs = rebuild_from_archive(plan)
            except (ReportError, TemplateError) as exc:
                logger.error("report generation failed for %s: %s", day.isoformat(), exc)
                self._notify(failure_message(day, str(exc)))
                return RunResult(day=day, month=month, ok=False, snapshot=snapshot, error=str(exc))

            reports = sorted(outputs.values())
            logger.info("run finished for %s: raw + %d report(s)", day.isoformat(), len(reports))
            self._notify(success_message(day, month, [path.name for path in reports]))
            return RunResult(day=day, month=month, ok=True, snapshot=snapshot, reports=reports)
        finally:
            # Guarantee the portal session is closed on both success and failure.
            self._close_session()

    def _close_session(self) -> None:
        """Close the portal session if the exporter owns one (idempotent)."""

        close = getattr(self.collector.exporter, "close", None)
        if close is not None:
            try:
                close()
            except Exception as exc:
                logger.warning("closing the portal session failed: %s: %s", type(exc).__name__, exc)

    def collect_and_archive(self, day: date) -> RawSnapshot:
        """Fetch both CSVs, then publish them as one complete pair."""

        settings = self.settings
        ap = self.collector.collect(settings.huawei.ap_filename)
        ssid = self.collector.collect(settings.huawei.ssid_filename)
        return publish_pair(settings.output_root, day, ap_bytes=ap.data, ssid_bytes=ssid.data)

    def _notify(self, message: str) -> None:
        try:
            self.notifier.send(message)
        except Exception as exc:  # a broken gateway must not mask the job result
            logger.warning("notification failed: %s: %s", type(exc).__name__, exc)


def rebuild_only(settings: Settings, month: tuple[int, int] | None = None) -> list[Path]:
    """Regenerate a month's reports from the archive without collecting."""

    plan = MonthPlan(
        year=month[0],
        month=month[1],
        template_dir=settings.template_dir,
        output_root=settings.output_root,
    )
    return sorted(rebuild_from_archive(plan).values())