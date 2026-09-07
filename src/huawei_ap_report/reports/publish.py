"""Report generation and atomic publication.

Every workbook is generated into a staging directory first. Only when all three
are readable and carry the expected tabs does the set get published, so a failure
part-way through never leaves two new reports beside one old one.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..archive import RawSnapshot, collect_snapshots
from ..errors import ReportError, TemplateError
from ..workbook import MONTHLY_TAB, days_in_month, load_template, resolve_template
from .connected import build_connected
from .detail import build_detail
from .graph import build_graph

logger = logging.getLogger(__name__)

#: Report key -> (template keyword, name used in the published filename).
REPORT_KINDS: dict[str, tuple[str, str]] = {
    "connected": ("connected", "Connected"),
    "detail": ("detail", "Detail"),
    "graph": ("graph", "Graph"),
}
SUFFIX = "-Report_{name}_AP_Huawei.xlsx"


def report_filename(year: int, month: int, kind: str) -> str:
    """``2026_09-Report_Connected_AP_Huawei.xlsx`` — the sample's exact naming."""

    _, name = REPORT_KINDS[kind]
    return f"{year}_{month:02d}{SUFFIX.format(name=name)}"


def report_paths(output_root: Path, year: int, month: int) -> dict[str, Path]:
    report_root = Path(output_root) / "report"
    return {
        kind: report_root / report_filename(year, month, kind) for kind in REPORT_KINDS
    }


@dataclass(frozen=True)
class MonthPlan:
    """Where the templates are, where the outputs go, and for which month."""

    year: int
    month: int
    template_dir: Path
    output_root: Path

    @property
    def month_key(self) -> tuple[int, int]:
        return (self.year, self.month)

    def templates(self) -> dict[str, Path]:
        return {
            kind: resolve_template(self.template_dir, keyword)
            for kind, (keyword, _) in REPORT_KINDS.items()
        }

    def outputs(self) -> dict[str, Path]:
        return report_paths(self.output_root, self.year, self.month)


def generate_month(plan: MonthPlan, snapshots: dict[date, RawSnapshot]) -> dict[str, Path]:
    """Build all three workbooks into staging and publish them as a set."""

    templates = plan.templates()
    outputs = plan.outputs()
    staging = Path(plan.output_root) / "report" / f".staging-{uuid.uuid4().hex[:8]}"
    staging.mkdir(parents=True, exist_ok=True)
    logger.info("building %04d-%02d reports from %d snapshot(s)", plan.year, plan.month, len(snapshots))

    try:
        for kind, template in templates.items():
            target = staging / outputs[kind].name
            shutil.copyfile(template, target)
            builder = {"connected": build_connected, "detail": build_detail, "graph": build_graph}[kind]
            builder(target, snapshots, month=plan.month_key)
            _verify(target, plan.year, plan.month)
        _publish(staging, outputs)
    except (ReportError, TemplateError):
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise ReportError(f"report generation failed for {plan.year:04d}-{plan.month:02d}: {exc}") from exc

    shutil.rmtree(staging, ignore_errors=True)
    logger.info("published %d report(s) for %04d-%02d", len(outputs), plan.year, plan.month)
    return outputs


def _verify(path: Path, year: int, month: int) -> None:
    """Reopen a generated workbook and confirm its tab set is intact."""

    try:
        workbook = load_template(path)
    except Exception as exc:
        raise ReportError(f"generated report is unreadable: {path.name} ({exc})") from exc

    missing = [MONTHLY_TAB] + [day.strftime("%d") for day in days_in_month(year, month)[:1]]
    for name in missing:
        if name not in workbook.sheetnames:
            raise ReportError(f"generated report {path.name} is missing the {name!r} tab")
    workbook.close()


def _publish(staging: Path, outputs: dict[str, Path]) -> None:
    """Move every staged workbook into place, rolling back on failure."""

    published: list[Path] = []
    backups: dict[Path, Path] = {}
    try:
        for kind, destination in outputs.items():
            staged = staging / destination.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                backup = destination.with_name(f".{destination.name}.previous")
                shutil.copyfile(destination, backup)
                backups[destination] = backup
            os.replace(staged, destination)
            published.append(destination)
    except Exception:
        for destination in published:
            backup = backups.get(destination)
            if backup is not None:
                os.replace(backup, destination)
        for backup in backups.values():
            backup.unlink(missing_ok=True)
        raise
    for backup in backups.values():
        backup.unlink(missing_ok=True)


def rebuild_from_archive(plan: MonthPlan) -> dict[str, Path]:
    """Regenerate a month's reports from whatever valid snapshots exist."""

    snapshots, skipped = collect_snapshots(plan.output_root, month=plan.month_key)
    if not snapshots:
        raise ReportError(
            f"no valid raw snapshots for {plan.year:04d}-{plan.month:02d} under "
            f"{Path(plan.output_root) / 'raw'}; nothing to report"
        )
    if skipped:
        logger.warning(
            "%d day(s) skipped for %04d-%02d: %s",
            len(skipped),
            plan.year,
            plan.month,
            ", ".join(day.isoformat() for day, _ in skipped),
        )
    return generate_month(plan, {snapshot.day: snapshot for snapshot in snapshots})