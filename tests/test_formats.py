from decimal import Decimal

import pytest

from huawei_ap_report.formats import (
    format_duration,
    format_percent,
    format_size_pair,
    parse_duration,
    parse_int,
    parse_percent,
    parse_ratio_pair,
    parse_size_pair,
    split_pair,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0%", 0.0),
        ("79%", 79.0),
        ("11.9%", 11.9),
        ("100.0%", 100.0),
        ("<1%(1/61369)", 1.0),
        ("<1%", 1.0),
        ("--", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_percent(raw, expected):
    assert parse_percent(raw) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(11.2, "11.20%"), (0.0, "0.00%"), (0.0666666, "0.07%"), (16.655, "16.66%")],
)
def test_format_percent_keeps_two_decimals(value, expected):
    assert format_percent(value) == expected


def test_parse_ratio_pair_extracts_counts():
    assert parse_ratio_pair("11.9%(7314/61369)") == (11.9, 7314, 61369)
    assert parse_ratio_pair("<1%(1/13979)") == (1.0, 1, 13979)
    assert parse_ratio_pair("0%") == (0.0, None, None)
    assert parse_ratio_pair("--") == (None, None, None)


def test_parse_percent_rejects_out_of_range_values():
    assert parse_percent("999%") is None
    assert parse_percent("-1%") is None
    assert parse_percent("0%") == 0.0
    assert parse_percent("100%") == 100.0
    # The portal's "<1%" sentinel is already bounded to 1.0.
    assert parse_percent("<1%") == 1.0


def test_parse_ratio_pair_rejects_impossible_counts():
    # Denominator zero.
    assert parse_ratio_pair("50%(1/0)") == (None, None, None)
    # Numerator above the denominator.
    assert parse_ratio_pair("50%(9/8)") == (None, None, None)
    # Both ends still valid.
    assert parse_ratio_pair("50%(8/8)") == (50.0, 8, 8)
    assert parse_ratio_pair("0%(0/8)") == (0.0, 0, 8)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0/0", (0.0, 0.0)),
        ("7.8M/3.1M", (7.8e6, 3.1e6)),
        ("12.3K/11.2K", (12300.0, 11200.0)),
        ("1014.2M/592.6M", (1014.2e6, 592.6e6)),
        ("243/238", (243.0, 238.0)),
        ("--", None),
    ],
)
def test_parse_size_pair(raw, expected):
    assert parse_size_pair(raw) == expected


@pytest.mark.parametrize(
    ("down", "up", "expected"),
    [
        (4.076e6, 1.264e6, "4.08M/1.26M"),
        (46.105e6, 12.204e6, "46.11M/12.20M"),
        (9514e6, 4269e6, "9.51G/4.27G"),
        (3471.0, 6247.0, "3.47K/6.25K"),
        (0.0, 0.0, "0/0"),
        (499.44e6, 241.943e6, "499.44M/241.94M"),
        (775.0, 158.0, "775/158"),
    ],
)
def test_format_size_pair_matches_sample_monthly_cells(down, up, expected):
    assert format_size_pair(down, up) == expected


def test_size_format_rounds_half_away_from_zero_not_bankers():
    # float(46.105) is really 46.104999...; Decimal(repr(...)) keeps the intent.
    assert Decimal(repr(46.105)) == Decimal("46.105")
    assert format_size_pair(4.6105e7, 4.6105e7) == "46.11M/46.11M"
    assert format_size_pair(46.1045, 46.1045) == "46/46"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1D:22H:46M:35S", ((1 * 24 + 22) * 60 + 46) * 60 + 35),
        ("9H:6M:48S", (9 * 60 + 6) * 60 + 48),
        ("0S", 0),
        ("bad", None),
        (None, None),
    ],
)
def test_parse_duration(raw, expected):
    assert parse_duration(raw) == expected


def test_parse_percent_anchors_fractional_part():
    # Test that the percentage regex properly anchors the fractional part
    # This ensures values like "<1%(1/61369)" are matched correctly
    from huawei_ap_report.formats import parse_percent
    
    # Valid matches
    assert parse_percent("<1%(1/61369)") == 1.0
    assert parse_percent("0%()") is None  # Malformed fraction should not match
    
    # Test the anchored regex pattern - the fraction should be part of the match
    from huawei_ap_report.formats import _PERCENT_RE
    
    match = _PERCENT_RE.match("<1%(1/61369)")
    assert match is not None
    assert match.group(1) == "<"
    assert match.group(2) == "1"
    
    # Ensure malformed input doesn't match
    match = _PERCENT_RE.match("<1%(1/61369")  # Missing closing parenthesis
    assert match is None
    
    match = _PERCENT_RE.match("1%")
    assert match is not None
    assert match.group(1) is None
    assert match.group(2) == "1"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (((1 * 24 + 4) * 60 + 2) * 60 + 49, "1D:4H:2M:49S"),
        (24 * 3600, "1D:0H:0M:0S"),
        (5 * 3600 + 54 * 60, "5H:54M:0S"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


def test_split_pair_rejects_single_value():
    assert split_pair("683/79") == ("683", "79")
    with pytest.raises(ValueError):
        split_pair("683")


@pytest.mark.parametrize(("raw", "expected"), [("456", 456), ("0", 0), ("--", None), ("", None), (None, None)])
def test_parse_int(raw, expected):
    assert parse_int(raw) == expected