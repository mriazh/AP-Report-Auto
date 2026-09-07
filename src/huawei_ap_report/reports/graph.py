"""Graph AP report — bucket summary plus the hidden chart source sheets.

The example Graph workbook's visible note tells the operator to paste data into a
sheet called ``Raw Data``. **That sheet does not exist.** The real chart inputs
are two hidden sheets, and that is what this writer fills:

``__GraphData``
    long format: one row per AP per day, prefixed with ``Report Date`` (13,657
    rows for 30 days of ~455 APs in the example).
``__GraphSummary``
    one row per day with histogram counts — STA quantity buckets, then a
    percentage bucket group per ratio metric — plus ``Count``, ``Total STA``,
    ``Avg CPU``, ``Avg Memory``; the final row is ``Monthly Average``, the mean
    of the daily rows.

Each visible tab keeps its KPI cells (``B6``, ``F6``, ``J6``, ``N6``) and its
chart source row 54, both of which are formulas. Row 54 has to be regenerated per
day, otherwise every chart keeps showing the template's values: the example uses
``INDEX(..., MATCH(DATE(...)))`` for day tabs and ``ROUND(... ,0)`` on the
Monthly tab. The bucket edges are read from the template's own row 53 / header
row instead of being hard-coded, so a retuned template is honoured.

The group layout is inferred from the labels: the leading run of buckets without
a ``%`` is the STA quantity histogram, and the percentage runs are assigned to
``STA Access Failure Ratio``, ``CPU Usage`` and ``Memory Usage`` in the order
those columns appear in the raw AP export.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date

from ..errors import TemplateError
from ..exports import AP_COLUMNS
from ..workbook import (
    MONTHLY_TAB,
    SheetBlock,
    days_in_month,
    ensure_day_tab,
    load_template,
    parse_day_tab,
    write_block,
)
from .common import as_excel_date, mean

logger = logging.getLogger(__name__)

GRAPH_DATA_SHEET = "__GraphData"
GRAPH_SUMMARY_SHEET = "__GraphSummary"
MONTHLY_AVERAGE_LABEL = "Monthly Average"
MASTER_TAB = "Master"

LABEL_ROW = 53
VALUE_ROW = 54
REPORT_DATE_HEADER = "Report Date"
KPI_SUMMARY_COLUMNS = {"B6": "AA", "F6": "AB", "J6": "AC", "N6": "AD"}
TOTAL_COLUMNS = ("Count", "Total STA", "Avg CPU", "Avg Memory")
#: Percentage bucket groups in raw-export column order.
PERCENT_GROUP_METRICS = ("STA Access Failure Ratio", "CPU Usage", "Memory Usage")
STA_METRIC = "STA Quantity"
#: Metrics behind the ``Avg CPU`` / ``Avg Memory`` KPI cells.
AVG_METRICS = ("CPU Usage", "Memory Usage")


def build_graph(template_path, snapshots: Mapping[date, object], *, month: tuple[int, int]) -> None:
    """Fill the hidden data sheets and rewrite the per-tab chart formulas."""

    workbook = load_template(template_path)
    for name in (GRAPH_DATA_SHEET, GRAPH_SUMMARY_SHEET):
        if name not in workbook.sheetnames:
            raise TemplateError(
                f"graph template is missing the {name} sheet; the chart formulas and the four "
                "per-tab bar charts need it (see config/README.md)"
            )

    monthly_snapshots = {day: snap for day, snap in snapshots.items() if (day.year, day.month) == month}
    layout = _summary_layout(workbook[GRAPH_SUMMARY_SHEET], _reference_tab(workbook))
    summary_rows = _summary_rows(layout, monthly_snapshots)
    row_of_day = {day: 2 + index for index, day in enumerate(sorted(monthly_snapshots))}
    monthly_row = 2 + len(summary_rows) - 1

    _write_summary(workbook[GRAPH_SUMMARY_SHEET], layout, summary_rows)
    _write_graph_data(workbook[GRAPH_DATA_SHEET], monthly_snapshots)

    for day in days_in_month(*month):
        tab = day.strftime("%d")
        if tab not in workbook.sheetnames:
            ensure_day_tab(workbook, tab)
        _write_tab(workbook[tab], layout, row_of_day.get(day), day=day if day in row_of_day else None)
    _write_tab(workbook[MONTHLY_TAB], layout, monthly_row, day=None, monthly_row=monthly_row)

    first_day = min(monthly_snapshots, default=None)
    if MASTER_TAB in workbook.sheetnames:
        # The example Master tab mirrors the first day of the month.
        _write_tab(workbook[MASTER_TAB], layout, row_of_day.get(first_day), day=first_day)

    workbook.save(template_path)
    logger.info(
        "graph report written for %04d-%02d from %d day(s)", month[0], month[1], len(monthly_snapshots)
    )


# --------------------------------------------------------------------------
# Bucket layout
# --------------------------------------------------------------------------


class SummaryLayout:
    """Which ``__GraphSummary`` column belongs to which histogram."""

    def __init__(self, groups: Sequence[tuple[str, Sequence[tuple[str, float, float, bool]]]]) -> None:
        from openpyxl.utils import get_column_letter

        self.groups = [(metric, list(buckets)) for metric, buckets in groups]
        self.columns = [metric for metric, _ in self.groups]
        self.width = sum(len(buckets) for _, buckets in self.groups)
        #: ``__GraphSummary`` letters for the buckets, in order (B..H, I..N, O..T, U..Z).
        self.summary_columns = tuple(get_column_letter(2 + index) for index in range(self.width))


def _reference_tab(workbook):
    """A visible tab to read the bucket layout from (any day tab will do)."""

    for tab in workbook.sheetnames:
        if parse_day_tab(tab):
            return workbook[tab]
    for name in (MONTHLY_TAB, MASTER_TAB):
        if name in workbook.sheetnames:
            return workbook[name]
    raise TemplateError(f"graph template has no tab carrying the chart bucket labels (row {LABEL_ROW})")


#: Range separators Excel writes instead of ``-`` once a locale/encoding has
#: mangled the label: ``?`` (ASCII 63) is the one seen in the real template,
#: the rest are the tilde/dash variants it can substitute.
RANGE_SEPARATORS = ("?", "~", "\uff5e", "\u223c", "\u2013", "\u2014")


def _parse_bucket_label(label: str) -> tuple[float, float, bool] | None:
    """``0`` -> closed at 0, ``1-5`` -> (1, 5], ``>50`` -> (50, inf)."""

    lowered = label.strip().lower()
    core = lowered[:-1] if lowered.endswith("%") else lowered

    if core.startswith(">"):
        try:
            return (float(core[1:]), float("inf"), False)
        except ValueError:
            return None

    normalised = core
    for separator in RANGE_SEPARATORS:
        normalised = normalised.replace(separator, "-")
    if "-" not in normalised:
        try:
            value = float(normalised)
        except ValueError:
            return None
        # A bare 0 bucket is closed at both ends; anything else is open at the
        # bottom so it cannot overlap the preceding bucket.
        return (0.0, 0.0, True) if value == 0 else (value, float("inf"), False)

    low, _, high = normalised.partition("-")
    try:
        return (float(low), float(high), False)
    except ValueError:
        return None


def _summary_layout(summary_sheet, visible_sheet) -> SummaryLayout:
    """Read the histogram layout from the template.

    ``__GraphSummary`` stores the buckets in one contiguous run (B..Z), which
    loses the group boundaries — three identical percentage groups sit side by
    side. The visible tabs keep them apart: column I is blank between the STA
    group and the first percentage group, and likewise between each pair of
    ratio groups. So the group widths come from the visible tab's labelled
    columns, and the labels themselves come from ``__GraphSummary``.
    """

    header = [
        summary_sheet.cell(row=1, column=column).value for column in range(1, summary_sheet.max_column + 1)
    ]
    if not header or str(header[0]).strip() != REPORT_DATE_HEADER:
        raise TemplateError(f"{GRAPH_SUMMARY_SHEET} column A must be {REPORT_DATE_HEADER!r}")

    labels = [str(value).strip() for value in header[1:] if value is not None]
    total_labels = [label for label in labels if label in TOTAL_COLUMNS]
    if total_labels:
        labels = labels[: len(labels) - len(total_labels)]
    parsed = [_parse_bucket_label(label) for label in labels]
    if not labels or any(bounds is None for bounds in parsed):
        bad = next(label for label, bounds in zip(labels, parsed, strict=True) if bounds is None)
        raise TemplateError(f"cannot read bucket label {bad!r} from {GRAPH_SUMMARY_SHEET}")

    widths = _bucket_group_widths(visible_sheet)
    metrics = (STA_METRIC, *PERCENT_GROUP_METRICS)
    if len(widths) != len(metrics):
        raise TemplateError(
            f"sheet {visible_sheet.title!r} row {LABEL_ROW} has {len(widths)} bucket groups but "
            f"{len(metrics)} are known ({', '.join(metrics)}); check the template layout"
        )

    groups: list[tuple[str, list[tuple[str, float, float, bool]]]] = []
    offset = 0
    for metric, width in zip(metrics, widths, strict=True):
        chunk = parsed[offset : offset + width]
        groups.append(
            (metric, [(label, *bounds) for label, bounds in zip(labels[offset : offset + width], chunk, strict=True)])
        )
        offset += width
    return SummaryLayout(groups)


def _bucket_group_widths(visible_sheet) -> list[int]:
    """Width of each histogram group on a visible tab.

    The group boundaries come from the tab's own bar charts — each chart's data
    reference is one histogram (``$B$54:$H$54``, ``$J$54:$O$54``, …), which is
    the only place the split is recorded, because the three percentage groups
    share identical labels and ``__GraphSummary`` stores them contiguously. Blank
    spacer columns are used when a tab carries no charts.
    """

    ranges = _chart_ranges(visible_sheet)
    if ranges:
        widths = [end - start + 1 for start, end in ranges]
    else:
        widths = _spacer_group_widths(visible_sheet)
    labelled = sum(1 for column in range(1, visible_sheet.max_column + 1) if visible_sheet.cell(row=LABEL_ROW, column=column).value is not None)
    if sum(widths) != labelled:
        raise TemplateError(
            f"sheet {visible_sheet.title!r}: chart bucket groups cover {sum(widths)} columns "
            f"but row {LABEL_ROW} has {labelled} labels"
        )
    return widths


def _chart_ranges(worksheet) -> list[tuple[int, int]]:
    """Sorted ``(first, last)`` column pairs from the tab's chart data references."""

    import re

    from openpyxl.utils import column_index_from_string

    spans: list[tuple[int, int]] = []
    for chart in worksheet._charts:
        for series in chart.series:
            reference = getattr(getattr(series, "val", None), "numRef", None)
            formula = getattr(reference, "f", None)
            if not formula:
                continue
            columns = re.findall(r"\$([A-Z]{1,3})\$?\d+", formula)
            if len(columns) >= 2:
                spans.append((column_index_from_string(columns[0]), column_index_from_string(columns[-1])))
    # One span per histogram; a chart with multiple series repeats the range.
    unique = sorted(set(spans))
    return unique


def _spacer_group_widths(visible_sheet) -> list[int]:
    """Group widths from blank spacer columns, for a tab without charts."""

    widths: list[int] = []
    current = 0
    for column in range(1, visible_sheet.max_column + 1):
        if visible_sheet.cell(row=LABEL_ROW, column=column).value is None:
            if current:
                widths.append(current)
                current = 0
            continue
        current += 1
    if current:
        widths.append(current)
    if not widths:
        raise TemplateError(f"sheet {visible_sheet.title!r} has no chart bucket labels in row {LABEL_ROW}")
    return widths


def _tally(values: Sequence[float | None], buckets: Sequence[tuple[str, float, float, bool]]) -> list[int]:
    counts = [0] * len(buckets)
    for value in values:
        if value is None:
            continue
        for index, (_, low, high, inclusive) in enumerate(buckets):
            if value < low or value > high:
                continue
            if not inclusive and value == low:
                continue
            counts[index] += 1
            break
    return counts


# --------------------------------------------------------------------------
# __GraphSummary
# --------------------------------------------------------------------------


def _metric_values(records: Sequence[Mapping[str, str]], column: str) -> list[float | None]:
    from .common import ap_cell_value

    return [
        float(value) if isinstance(value := ap_cell_value(record, column), (int, float)) else None
        for record in records
    ]


def _summary_rows(layout: SummaryLayout, snapshots: Mapping[date, object]) -> list[list[object]]:
    rows: list[list[object]] = []
    for day in sorted(snapshots):
        records = snapshots[day].ap.records
        values: list[object] = [as_excel_date(day)]
        for metric, buckets in layout.groups:
            values.extend(_tally(_metric_values(records, metric), buckets))
        sta = [value for value in _metric_values(records, STA_METRIC) if value is not None]
        totals: list[object] = [len(records), sum(sta) if sta else 0]
        for metric in AVG_METRICS:
            totals.append(mean(_metric_values(records, metric)))
        rows.append([*values, *totals])

    if not rows:
        return []

    averages: list[float | None] = []
    for column in range(1, len(rows[0])):
        values = [row[column] for row in rows]
        averages.append(mean(value for value in values if isinstance(value, (int, float))))
    return [*rows, [MONTHLY_AVERAGE_LABEL, *averages]]


def _write_summary(summary_sheet, layout: SummaryLayout, rows: list[list[object]]) -> None:
    block = SheetBlock(header_row=1, first_data_row=2, last_column=1 + layout.width + len(TOTAL_COLUMNS))
    write_block(summary_sheet, block, rows)


# --------------------------------------------------------------------------
# __GraphData
# --------------------------------------------------------------------------


def _write_graph_data(data_sheet, snapshots: Mapping[date, object]) -> None:
    header = [data_sheet.cell(row=1, column=column).value for column in range(1, 1 + len(AP_COLUMNS) + 1)]
    if str(header[0]).strip() != REPORT_DATE_HEADER:
        raise TemplateError(f"{GRAPH_DATA_SHEET} column A must be {REPORT_DATE_HEADER!r}")
    from .common import ap_row_values

    rows: list[list[object]] = []
    for day in sorted(snapshots):
        stamp = as_excel_date(day)
        rows.extend([stamp, *ap_row_values(record)] for record in snapshots[day].ap.records)
    write_block(data_sheet, SheetBlock(header_row=1, first_data_row=2, last_column=1 + len(AP_COLUMNS)), rows)


# --------------------------------------------------------------------------
# Visible tabs
# --------------------------------------------------------------------------


def _write_tab(
    worksheet,
    layout: SummaryLayout,
    summary_row: int | None,
    *,
    day: date | None,
    monthly_row: int | None = None,
) -> None:
    """Point the tab's KPI cells and chart source row at one ``__GraphSummary`` row.

    ``monthly_row`` set means the Monthly tab (rounded, as the example does).
    ``summary_row=None`` means the tab has no snapshot for that day: its KPI cells
    and chart row are cleared rather than left pointing at another day's data.
    """

    visible_columns = _bucket_value_columns(worksheet, layout)

    if summary_row is None:
        for cell in KPI_SUMMARY_COLUMNS:
            worksheet[cell] = None
        for column in visible_columns:
            worksheet[f"{column}{VALUE_ROW}"] = None
        return

    for cell, summary_column in KPI_SUMMARY_COLUMNS.items():
        worksheet[cell] = f"=__GraphSummary!${summary_column}${summary_row}"

    for column, summary_column in zip(visible_columns, layout.summary_columns, strict=True):
        if monthly_row is not None:
            worksheet[f"{column}{VALUE_ROW}"] = f"=ROUND(__GraphSummary!{summary_column}{monthly_row}, 0)"
        else:
            worksheet[f"{column}{VALUE_ROW}"] = (
                f"=INDEX(__GraphSummary!{summary_column}:{summary_column}, "
                f"MATCH(DATE({day.year},{day.month},{day.day}), __GraphSummary!$A:$A, 0))"
            )


def _bucket_value_columns(worksheet, layout: SummaryLayout) -> list[str]:
    """Visible columns holding chart values: every labelled column in row 53."""

    from openpyxl.utils import get_column_letter

    columns: list[str] = []
    for column in range(1, worksheet.max_column + 1):
        if worksheet.cell(row=LABEL_ROW, column=column).value is not None:
            columns.append(get_column_letter(column))
    if not columns:
        raise TemplateError(f"sheet {worksheet.title!r} has no chart bucket labels in row {LABEL_ROW}")
    if len(columns) != layout.width:
        raise TemplateError(
            f"sheet {worksheet.title!r} row {LABEL_ROW} has {len(columns)} bucket labels but "
            f"{GRAPH_SUMMARY_SHEET} has {layout.width}"
        )
    return columns


__all__ = [
    "GRAPH_DATA_SHEET",
    "GRAPH_SUMMARY_SHEET",
    "MONTHLY_AVERAGE_LABEL",
    "SummaryLayout",
    "build_graph",
]