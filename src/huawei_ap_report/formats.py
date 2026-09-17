"""Value parsing/formatting shared by the raw CSV layer and the report writers.

The Huawei portal emits human-readable strings (``79%``, ``683/79``,
``7.8M/3.1M``, ``<1%(1/61369)``, ``1D:22H:46M:35S``). The monthly report tabs
average those values, so parsing has to be lossless and formatting has to match
the workbook samples exactly.
"""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

__all__ = [
    "format_duration",
    "format_percent",
    "format_size_pair",
    "parse_duration",
    "parse_int",
    "parse_percent",
    "parse_ratio_pair",
    "parse_size_pair",
    "split_pair",
]

_UNITS = {"": 1, "K": 1_000, "M": 1_000_000, "G": 1_000_000_000}
_UNIT_FACTORS = {"": 1, "K": 1_000, "M": 1_000_000, "G": 1_000_000_000}

_SIZE_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)([KMG]?)$")
_PERCENT_RE = re.compile(r"^(<)?([0-9]+(?:\.[0-9]+)?)%(?:\([0-9]+/[0-9]+\))?$")
_DURATION_RE = re.compile(
    r"^(?:(?P<d>[0-9]+)D:)?(?:(?P<h>[0-9]+)H:)?(?:(?P<m>[0-9]+)M:)?(?P<s>[0-9]+)S$"
)


def split_pair(value: str) -> tuple[str, str]:
    """Split a ``down/up`` cell into its two halves."""

    parts = value.split("/")
    if len(parts) != 2:
        raise ValueError(f"expected a down/up pair, got {value!r}")
    return parts[0].strip(), parts[1].strip()


def parse_percent(value: str | None) -> float | None:
    """Return a percentage as a number.

    ``<1%`` means "below one percent"; the portal only reports it when the
    measured ratio is nonzero but rounds to zero, and the sample monthly tabs
    treat it as exactly ``1``. Values that are not percentages (``--``, empty)
    return ``None`` so they can be excluded from averages instead of counted
    as zero.
    """

    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    match = _PERCENT_RE.match(text)
    if not match:
        return None
    bounded, number = match.group(1), match.group(2)
    if bounded:
        return 1.0
    number = float(number)
    if not 0.0 <= number <= 100.0:
        return None
    return number


def format_percent(value: float) -> str:
    """Render a percentage the way the Monthly tabs do: two decimals."""

    return f"{_round2(value):.2f}%"


def parse_ratio_pair(value: str | None) -> tuple[float | None, float | None, float | None]:
    """Parse ``11.9%(7314/61369)`` into ``(percent, numerator, denominator)``.

    ``<1%(1/61369)`` yields ``(1.0, 1, 61369)``. Plain ``0%`` yields
    ``(0.0, None, None)``.
    """

    if value is None:
        return None, None, None
    text = value.strip()
    percent = parse_percent(text)
    if percent is None:
        return None, None, None
    fractions = re.search(r"\(([0-9]+)/([0-9]+)\)", text)
    if not fractions:
        return percent, None, None
    numerator, denominator = int(fractions.group(1)), int(fractions.group(2))
    if denominator <= 0 or numerator < 0 or numerator > denominator:
        return None, None, None
    return percent, numerator, denominator


def parse_size_pair(value: str | None) -> tuple[float, float] | None:
    """Parse ``7.8M/3.1M`` (or ``0/0``) into raw counts."""

    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        down, up = split_pair(text)
        return _parse_size(down), _parse_size(up)
    except ValueError:
        return None


def _parse_size(token: str) -> float:
    match = _SIZE_RE.match(token.strip())
    if not match:
        raise ValueError(f"not a size: {token!r}")
    return float(match.group(1)) * _UNIT_FACTORS[match.group(2)]


def _round2(value: float) -> Decimal:
    """Round to two decimals, half away from zero.

    ``round()`` uses banker's rounding and float representation makes values
    such as ``46.105`` land on ``46.10``; the sample workbooks show ``46.11M``.
    """

    return Decimal(repr(float(value))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_size_pair(down: float, up: float) -> str:
    """Render an averaged ``down/up`` pair with unit suffixes and two decimals."""

    return f"{_format_size(down)}/{_format_size(up)}"





def _format_size(value: float) -> str:
    """Render one magnitude: a unit suffix plus two decimals, or a whole number.

    ``4.076e6`` becomes ``4.08M`` and ``775.0`` becomes ``775`` — the sample
    workbooks show plain integers for the AP wired-side throughput (Kbps) and
    two decimals for the SSID byte/frame counters.
    """

    magnitude = abs(value)
    for unit in ("G", "M", "K"):
        if magnitude >= _UNITS[unit]:
            scaled = Decimal(repr(value / _UNITS[unit])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            return f"{scaled:.2f}{unit}"
    return str(_round_half_up_int(value))


def _round_half_up_int(value: float) -> int:
    return int(Decimal(repr(float(value))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_duration(value: str | None) -> int | None:
    """Parse ``1D:22H:46M:35S`` / ``9H:6M:48S`` into whole seconds."""

    if value is None:
        return None
    match = _DURATION_RE.match(value.strip())
    if not match:
        return None
    days = int(match.group("d") or 0)
    hours = int(match.group("h") or 0)
    minutes = int(match.group("m") or 0)
    seconds = int(match.group("s") or 0)
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def format_duration(seconds: float) -> str:
    """Render seconds as ``1D:4H:2M:49S`` (days omitted when zero).

    Rounds half away from zero rather than with ``round()``'s banker's rule, so
    an average that lands exactly on ``.5`` s does not flip with the parity of
    the second.
    """

    total = _round_half_up_int(seconds)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}D:{hours}H:{minutes}M:{secs}S"
    return f"{hours}H:{minutes}M:{secs}S"


def parse_int(value: str | None) -> int | None:
    """Parse a plain integer cell, returning ``None`` for placeholders."""

    if value is None:
        return None
    text = value.strip()
    if not text or text == "--":
        return None
    try:
        return int(text)
    except ValueError:
        return None