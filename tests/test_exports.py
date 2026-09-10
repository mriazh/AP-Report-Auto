from __future__ import annotations

import pytest

from fixtures import ap_csv, ap_record, header_only_csv, ssid_csv, ssid_record
from huawei_ap_report.errors import ExportValidationError
from huawei_ap_report.exports import (
    AP_COLUMNS,
    SSID_COLUMNS,
    load_ap_export,
    load_ssid_export,
    parse_export,
    required_columns_for,
    validate_named_export,
)


def test_ap_export_strips_bom_tabs_and_keeps_values(tmp_path):
    path = tmp_path / "apInfo.csv"
    path.write_bytes(ap_csv([ap_record(ap_id=7, name="AP-ONE", sta=12, cpu="13%")]))

    export = load_ap_export(path)

    assert export.columns == AP_COLUMNS
    assert len(export) == 1
    record = export.records[0]
    assert record["AP name"] == "AP-ONE"
    assert record["STA Quantity"] == "12"
    assert record["CPU Usage"] == "13%"
    assert record["Installation location"] == ""
    assert record["Central AP name"] == "--"


def test_ssid_export_keeps_arrow_headers_and_ratio_text(tmp_path):
    path = tmp_path / "ssidInfo.csv"
    path.write_bytes(ssid_csv([ssid_record("A"), ssid_record("B")]))

    export = load_ssid_export(path)

    assert export.columns == SSID_COLUMNS
    assert [record["SSID"] for record in export.records] == ["A", "B"]
    assert export.records[0]["Downlink retransmission ratio"] == "9.1%(2399/26403)"


def test_missing_required_column_is_rejected():
    truncated = AP_COLUMNS[:-1]

    with pytest.raises(ExportValidationError) as excinfo:
        parse_export(ap_csv(columns=truncated).decode("utf-8-sig"), kind="AP", required_columns=AP_COLUMNS)

    assert "missing columns" in str(excinfo.value)
    assert "Poweroff restart count" in str(excinfo.value)


def test_empty_file_is_rejected():
    with pytest.raises(ExportValidationError, match="empty"):
        parse_export("", kind="AP", required_columns=AP_COLUMNS)


def test_header_only_file_is_rejected():
    with pytest.raises(ExportValidationError, match="no data rows"):
        parse_export(header_only_csv(AP_COLUMNS).decode("utf-8-sig"), kind="AP", required_columns=AP_COLUMNS)


def test_ragged_row_is_rejected():
    text = ap_csv([ap_record()]).decode("utf-8-sig").rstrip("\n")
    text += "\n" + '"'.join(["\t1"] * (len(AP_COLUMNS) - 2))

    with pytest.raises(ExportValidationError) as excinfo:
        parse_export(text, kind="AP", required_columns=AP_COLUMNS)

    assert "expected" in str(excinfo.value)


def test_missing_file_reports_path(tmp_path):
    with pytest.raises(ExportValidationError, match="not found"):
        load_ap_export(tmp_path / "absent.csv")


def test_value_lookup_tolerates_dropped_column():
    export = parse_export(ap_csv().decode("utf-8-sig"), kind="AP", required_columns=AP_COLUMNS)
    assert export.value(export.records[0], "No Such Column", default="-") == "-"


def test_required_columns_for_known_names():
    assert required_columns_for("apInfo.csv") == (AP_COLUMNS, "AP")
    assert required_columns_for("SSIDINFO.CSV") == (SSID_COLUMNS, "SSID")
    with pytest.raises(ExportValidationError):
        required_columns_for("other.csv")


def test_validate_named_export_uses_the_name_to_choose_the_schema():
    assert validate_named_export("apInfo.csv", ap_csv()).kind == "AP"
    assert validate_named_export("ssidInfo.csv", ssid_csv()).kind == "SSID"
    # An AP payload validated under the SSID name is rejected, not silently accepted.
    with pytest.raises(ExportValidationError, match="missing columns"):
        validate_named_export("ssidInfo.csv", ap_csv())
    with pytest.raises(ExportValidationError, match="unknown export filename"):
        validate_named_export("mystery.csv", ap_csv())


def test_decode_export_rejects_invalid_utf8():
    # Test that invalid UTF-8 data is properly rejected with ExportValidationError
    invalid_utf8 = b"\xff\xfe\x00\x00"  # Invalid UTF-8 bytes
    
    with pytest.raises(ExportValidationError, match="invalid UTF-8 encoding"):
        from huawei_ap_report.exports import decode_export
        decode_export(invalid_utf8)