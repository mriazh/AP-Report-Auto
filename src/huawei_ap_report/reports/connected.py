"""Connected AP report — the SSID tab set.

Layout taken from the supplied example: title in ``A2``, header in row 4, SSID
rows from row 5, one tab per day plus ``Monthly``. ``Master`` is left exactly as
the template has it: in the example it carries the title and header and no rows,
so this writer does not invent rows for it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date

from ..exports import SSID_COLUMNS
from ..workbook import (
    MONTHLY_TAB,
    SheetBlock,
    column_of,
    days_in_month,
    ensure_day_tab,
    load_template,
    parse_day_tab,
    template_header_row,
    write_block,
)
from .common import ssid_daily_values, ssid_monthly_values

logger = logging.getLogger(__name__)

REQUIRED_HEADERS = ("SSID", "User Quantity", "AP Quantity")


def _block_for(worksheet) -> SheetBlock:
    header_row = template_header_row(worksheet, keyword="SSID", required=REQUIRED_HEADERS)
    return SheetBlock.below(header_row, last_column=column_of(worksheet, header_row, SSID_COLUMNS[-1]))


def build_connected(template_path, snapshots: Mapping[date, object], *, month: tuple[int, int]) -> None:
    """Fill day tabs and the Monthly tab of a Connected workbook in place."""

    workbook = load_template(template_path)

    for day in days_in_month(*month):
        tab = day_tab_name_for(day)
        if tab not in workbook.sheetnames:
            ensure_day_tab(workbook, tab)
        worksheet = workbook[tab]
        snapshot = snapshots.get(day)
        rows = [] if snapshot is None else [ssid_daily_values(record) for record in snapshot.ssid.records]
        write_block(worksheet, _block_for(worksheet), rows)

    monthly = workbook[MONTHLY_TAB]
    write_block(monthly, _block_for(monthly), monthly_rows(snapshots))

    workbook.save(template_path)
    logger.info("connected report written for %04d-%02d from %d day(s)", month[0], month[1], len(snapshots))


def day_tab_name_for(day: date) -> str:
    return day.strftime("%d")


def monthly_rows(snapshots: Mapping[date, object]) -> list[list[object]]:
    """One Monthly row per SSID, in the order the portal first reported them.

    The example Monthly tab follows the raw export order (GMF-IoT … GMF-Mobile-
    Huawei, then Download Posturing), so first-appearance order is kept instead
    of an alphabetical sort.
    """

    by_ssid: dict[str, list[dict[str, str]]] = {}
    for day in sorted(snapshots):
        for record in snapshots[day].ssid.records:
            by_ssid.setdefault(record.get(SSID_COLUMNS[0], ""), []).append(record)
    return [ssid_monthly_values(records) for records in by_ssid.values()]


__all__ = ["build_connected", "monthly_rows", "parse_day_tab"]