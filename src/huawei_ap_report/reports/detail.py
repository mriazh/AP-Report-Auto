"""Detail AP report — the per-AP tab set.

Layout taken from the supplied example: header in row 1, AP rows from row 2,
one tab per day plus ``Monthly`` and a ``Master`` that holds the header only.
Metric columns become numbers, so the Monthly averages are computed here and
written as values (the example workbooks contain no formulas either).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date

from ..exports import AP_COLUMNS
from ..workbook import (
    MONTHLY_TAB,
    SheetBlock,
    column_of,
    days_in_month,
    ensure_day_tab,
    load_template,
    template_header_row,
    write_block,
)
from .common import (
    AP_MEAN_COLUMNS,
    AP_NAME_COLUMN,
    LOGIN_PERIOD_COLUMN,
    THROUGHPUT_COLUMN,
    ap_login_period_value,
    ap_mean_value,
    ap_row_values,
    ap_throughput_value,
)

logger = logging.getLogger(__name__)

REQUIRED_HEADERS = ("AP name", "STA Quantity", "CPU Usage")


def _block_for(worksheet) -> tuple[SheetBlock, dict[str, int]]:
    header_row = template_header_row(worksheet, keyword="AP", required=REQUIRED_HEADERS)
    columns = {name: column_of(worksheet, header_row, name) for name in AP_COLUMNS}
    return SheetBlock.below(header_row, last_column=max(columns.values())), columns


def build_detail(template_path, snapshots: Mapping[date, object], *, month: tuple[int, int]) -> None:
    """Fill day tabs and the Monthly tab of a Detail workbook in place."""

    workbook = load_template(template_path)

    for day in days_in_month(*month):
        tab = day.strftime("%d")
        if tab not in workbook.sheetnames:
            ensure_day_tab(workbook, tab)
        worksheet = workbook[tab]
        block, _ = _block_for(worksheet)
        snapshot = snapshots.get(day)
        rows = [] if snapshot is None else [ap_row_values(record) for record in snapshot.ap.records]
        write_block(worksheet, block, rows)

    monthly = workbook[MONTHLY_TAB]
    block, _ = _block_for(monthly)
    write_block(monthly, block, monthly_rows(snapshots))

    workbook.save(template_path)
    logger.info("detail report written for %04d-%02d from %d day(s)", month[0], month[1], len(snapshots))


def monthly_rows(snapshots: Mapping[date, object]) -> list[list[object]]:
    """One Monthly row per AP, in the order the portal first reported it."""

    by_ap: dict[str, list[list[object]]] = {}
    for day in sorted(snapshots):
        for record in snapshots[day].ap.records:
            by_ap.setdefault(record.get(AP_NAME_COLUMN, ""), []).append(ap_row_values(record))

    rows: list[list[object]] = []
    for daily_rows in by_ap.values():
        columns = list(zip(*daily_rows, strict=True))
        row: list[object] = []
        for name, values in zip(AP_COLUMNS, columns, strict=True):
            if name in AP_MEAN_COLUMNS:
                row.append(ap_mean_value(values))
            elif name == LOGIN_PERIOD_COLUMN:
                row.append(ap_login_period_value(values))
            elif name == THROUGHPUT_COLUMN:
                row.append(ap_throughput_value(values))
            else:
                row.append(next((value for value in reversed(values) if value not in (None, "")), None))
        rows.append(row)
    return rows