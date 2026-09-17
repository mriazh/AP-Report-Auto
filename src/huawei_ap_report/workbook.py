"""Template discovery and workbook cell plumbing (openpyxl).

The supplied templates own the layout: sheet names, titles, headers, styles,
number formats, charts and hidden sheets. This module only locates a template
and writes/clears cell values, which keeps template characteristics intact and
makes the "don't publish a half-written workbook" rule easy to test.
"""

from __future__ import annotations

import logging
from copy import copy
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from openpyxl.workbook.workbook import Workbook

from .errors import TemplateError

logger = logging.getLogger(__name__)

MONTHLY_TAB = "Monthly"
MASTER_TAB = "Master"
DAY_TAB_DIGITS = 2


@dataclass(frozen=True)
class SheetBlock:
    """A rectangular data region: header row, first data row, last column."""

    header_row: int
    first_data_row: int
    last_column: int

    @classmethod
    def below(cls, header_row: int, *, last_column: int) -> "SheetBlock":
        return cls(header_row=header_row, first_data_row=header_row + 1, last_column=last_column)

    @property
    def max_rows_written(self) -> int:
        return self.first_data_row - 1


def day_tab_name(day: date) -> str:
    return day.strftime("%d")


def parse_day_tab(name: str) -> int | None:
    """Parse a ``01``-style tab name into a day-of-month number."""

    if len(name) != DAY_TAB_DIGITS or not name.isdigit():
        return None
    day = int(name)
    return day if 1 <= day <= 31 else None


def days_in_month(year: int, month: int) -> list[date]:
    from calendar import monthrange

    return [date(year, month, number) for number in range(1, monthrange(year, month)[1] + 1)]


def resolve_template(template_dir: Path, keyword: str) -> Path:
    """Find the newest workbook in ``template_dir`` whose name contains ``keyword``.

    Names are matched loosely so ``2026_09-Report_Connected_AP_Huawei.xlsx`` and
    ``connected.xlsx`` both work. The templates live outside version control, so
    a missing file is a setup step, not a code error — the message says exactly
    what to place.
    """

    directory = Path(template_dir)
    if not directory.is_dir():
        raise TemplateError(
            f"template directory not found: {directory} (set TEMPLATE_DIR or create it)"
        )
    candidates = sorted(
        (path for path in directory.glob("*.xlsx") if keyword in path.stem.lower()),
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    if not candidates:
        raise TemplateError(
            f"no template matching {keyword!r} in {directory}: place a {keyword} workbook there "
            f"(see config/README.md)"
        )
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise TemplateError(
            f"ambiguous template matching {keyword!r} in {directory}: found {len(candidates)} "
            f"candidates ({names}); keep exactly one template per report kind"
        )
    resolved = candidates[-1]
    logger.info("using %s template: %s", keyword, resolved)
    return resolved


def load_template(path: Path) -> Workbook:
    """Load a template workbook, keeping its formulas."""

    path = Path(path)
    try:
        return load_workbook(path)
    except Exception as exc:  # pragma: no cover - openpyxl error surface is wide
        raise TemplateError(f"cannot open template {path}: {exc}") from exc


def template_header_row(worksheet, *, keyword: str, required: tuple[str, ...]) -> int:
    """Find the header row inside ``[1, 8]`` by looking for ``required`` labels."""

    for row in range(1, min(9, worksheet.max_row + 1)):
        values = [str(worksheet.cell(row=row, column=column).value or "").strip() for column in range(1, worksheet.max_column + 1)]
        if all(any(label == value for value in values) for label in required):
            return row
    raise TemplateError(f"template sheet {worksheet.title!r} has no header row with {keyword} labels")


def column_of(worksheet, header_row: int, header: str) -> int:
    """Locate a header label by exact match, tolerating the tab prefix."""

    wanted = header.strip()
    for column in range(1, worksheet.max_column + 1):
        value = worksheet.cell(row=header_row, column=column).value
        if value is not None and str(value).strip() == wanted:
            return column
    raise TemplateError(f"template sheet {worksheet.title!r} has no {header!r} column in row {header_row}")


def copy_style(source, target) -> None:
    """Copy number format and appearance from one cell to another."""

    if source.has_style:
        target.font = copy(source.font)
        target.fill = copy(source.fill)
        target.border = copy(source.border)
        target.alignment = copy(source.alignment)
        target.number_format = source.number_format
        target.protection = copy(source.protection)


def write_block(worksheet, block: SheetBlock, rows: list[list[object]]) -> None:
    """Write ``rows`` starting at ``block.first_data_row`` and clear the rest.

    Rows inside the template's own extent keep their template styling. Rows past
    it borrow the style of the row above, so a 456-AP day fills a 456-row
    template exactly as the sample workbooks do. Cells below the last written
    row are emptied, so a shorter day never shows the previous day's numbers.
    """

    template_limit = worksheet.max_row
    for offset, values in enumerate(rows):
        row_number = block.first_data_row + offset
        beyond_template = row_number > template_limit
        for column in range(1, block.last_column + 1):
            cell = worksheet.cell(row=row_number, column=column)
            if beyond_template and row_number > 1:
                copy_style(worksheet.cell(row=row_number - 1, column=column), cell)
            cell_value = values[column - 1] if column - 1 < len(values) else None
            # Sanitize string values starting with formula prefix characters
            if isinstance(cell_value, str) and cell_value and cell_value[0] in ('=', '+', '-', '@'):
                cell_value = "'" + cell_value
            cell.value = cell_value

    first_unused = block.first_data_row + len(rows)
    if worksheet.max_row >= first_unused:
        # Remove the leftover rows outright: blank cells would still count
        # towards max_row and make a stale-data check unreliable.
        worksheet.delete_rows(first_unused, worksheet.max_row - first_unused + 1)


def ensure_day_tab(workbook: Workbook, tab: str) -> object:
    """Return the day tab, creating it from the last day tab when missing.

    A 30-day template has no ``31`` tab. ``copy_worksheet`` brings over values,
    styles, dimensions and hidden state, but not charts — a created tab is
    therefore chart-less, which ``config/README.md`` tells the operator about.
    """

    if tab in workbook.sheetnames:
        return workbook[tab]

    existing = [name for name in workbook.sheetnames if parse_day_tab(name)]
    if not existing:
        raise TemplateError(f"template has no day tabs (01..31) to copy for {tab!r}")
    donor = workbook[max(existing, key=parse_day_tab)]
    created = workbook.copy_worksheet(donor)
    created.title = tab
    logger.warning("template has no %r tab; copied it from %r (charts are not copied)", tab, donor.title)
    return created


