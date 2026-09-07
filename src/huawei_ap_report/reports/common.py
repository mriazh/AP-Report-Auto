"""Value conversion and averaging shared by the Detail, Connected and Graph writers.

Reducers follow the supplied example workbooks, which were checked against the
sample raw data:

* ``STA Quantity``, ``AP Quantity``, ``STA Access Failure Ratio``, ``Logout ratio``,
  ``CPU Usage``, ``Memory Usage``, ``Total Restart Count`` and
  ``Poweroff restart count`` are monthly means; percentages are stored as plain
  numbers (``72`` means 72%).
* ``<1%`` counts as ``1`` — the sample Monthly tabs can only be reproduced that
  way (GMF-IoT loss averages ``0.13%`` over 30 days).
* ``AP Quantity`` in the Connected Monthly tab is the mean rounded to a whole
  number; throughput/frame pairs are averaged per direction and rendered with two
  decimals and a ``K``/``M``/``G`` suffix.
* ``Login period`` is the mean duration, re-rendered as ``1D:4H:2M:49S``.
* Text columns are taken from the most recent day that reported the AP.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterable, Sequence

from ..errors import ExportValidationError
from ..exports import AP_COLUMNS, SSID_COLUMNS
from ..formats import (
    format_duration,
    format_percent,
    format_size_pair,
    parse_duration,
    parse_int,
    parse_percent,
    parse_ratio_pair,
    parse_size_pair,
)

AP_INTEGER_COLUMNS: tuple[str, ...] = (
    "AP ID",
    "STA Quantity",
    "Total Restart Count",
    "Poweroff restart count",
)
AP_PERCENT_COLUMNS: tuple[str, ...] = (
    "STA Access Failure Ratio",
    "Logout ratio",
    "CPU Usage",
    "Memory Usage",
)
AP_MEAN_COLUMNS: tuple[str, ...] = AP_INTEGER_COLUMNS + AP_PERCENT_COLUMNS
THROUGHPUT_COLUMN = "Wired-side throughput(Kbps) ↓↑"
LOGIN_PERIOD_COLUMN = "Login period"
AP_NAME_COLUMN = "AP name"


def mean(values: Iterable[float | None]) -> float | None:
    """Arithmetic mean ignoring ``None``; ``None`` when nothing is left."""

    numbers = [value for value in values if value is not None]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _round_half_up(value: float) -> int:
    from decimal import ROUND_HALF_UP, Decimal

    return int(Decimal(repr(float(value))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def ap_cell_value(record: dict[str, str], column: str) -> object:
    """One AP value as the workbooks store it (numbers for metrics, text else)."""

    raw = record.get(column, "")
    if column in AP_INTEGER_COLUMNS:
        return parse_int(raw)
    if column in AP_PERCENT_COLUMNS:
        percent = parse_percent(raw)
        return None if percent is None else int(percent) if percent == int(percent) else percent
    return raw or None


def ap_row_values(record: dict[str, str]) -> list[object]:
    return [ap_cell_value(record, column) for column in AP_COLUMNS]


def ap_mean_value(values: Sequence[object]) -> object:
    """Monthly cell value for one AP column.

    Metric columns become the exact mean of the days the AP was reported (the
    sample Monthly tab stores ``4.3`` and ``64.96666666666667``, i.e. no
    rounding); text columns take the most recent non-empty value.
    """

    if all(isinstance(value, (int, float)) or value is None for value in values):
        averaged = mean(values)
        if averaged is None:
            return None
        return int(averaged) if float(averaged).is_integer() else averaged
    return next((value for value in reversed(values) if value not in (None, "")), None)


def ap_login_period_value(values: Sequence[object]) -> str | None:
    durations = [parse_duration(value) if isinstance(value, str) else None for value in values]
    averaged = mean(durations)
    return None if averaged is None else format_duration(averaged)


def ap_throughput_value(values: Sequence[object]) -> str | None:
    pairs = [parse_size_pair(value) if isinstance(value, str) else None for value in values]
    usable = [pair for pair in pairs if pair is not None]
    if not usable:
        return None
    down = mean(pair[0] for pair in usable)
    up = mean(pair[1] for pair in usable)
    return format_size_pair(down, up)


# --------------------------------------------------------------------------
# SSID (Connected report)
# --------------------------------------------------------------------------

SSID_INT_COLUMNS = ("User Quantity", "AP Quantity")
SSID_PAIR_COLUMNS = ("Valid Throughput (bps) ↓↑", "Frame quantity ↓↑")
SSID_RATIO_COLUMNS = ("Downlink retransmission ratio", "Downlink packet loss ratio")


def ssid_daily_values(record: dict[str, str]) -> list[object]:
    """Daily tabs keep the portal's strings, with the two counts as numbers.

    The example daily tabs hold ``55``/``449`` as numbers while the throughput
    and ratio cells stay verbatim text (``7.8M/3.1M``, ``9.1%(2399/26403)``).
    """

    values: list[object] = []
    for column in SSID_COLUMNS:
        raw = record.get(column, "")
        if column in SSID_INT_COLUMNS:
            values.append(parse_int(raw))
        else:
            values.append(raw)
    return values


def ssid_monthly_values(records: Sequence[dict[str, str]]) -> list[object]:
    """Connected ``Monthly`` row for one SSID."""

    if not records:
        raise ExportValidationError("cannot build a Monthly row without SSID records")

    users = mean(float(record["User Quantity"]) for record in records)
    aps = _round_half_up(mean(float(record["AP Quantity"]) for record in records))
    values: list[object] = [records[0].get(SSID_COLUMNS[0], "")]
    values.append(users)
    values.append(aps)
    for column in SSID_PAIR_COLUMNS:
        pairs = [parse_size_pair(record.get(column)) for record in records]
        usable = [pair for pair in pairs if pair is not None]
        values.append(None if not usable else format_size_pair(mean(p[0] for p in usable), mean(p[1] for p in usable)))
    for column in SSID_RATIO_COLUMNS:
        percents = [parse_ratio_pair(record.get(column))[0] for record in records]
        averaged = mean(percents)
        values.append("" if averaged is None else format_percent(averaged))
    return values


def as_excel_date(value: _dt.date) -> _dt.datetime:
    return _dt.datetime(value.year, value.month, value.day)