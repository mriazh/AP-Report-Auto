from __future__ import annotations

from pathlib import Path

import pytest
from fixtures import build_templates
from huawei_ap_report.errors import TemplateError
from huawei_ap_report.workbook import ensure_day_tab, resolve_template
from openpyxl import Workbook


def test_resolve_template_ambiguous_when_two_match(tmp_path):
    build_templates(tmp_path / "templates")
    # A second connected workbook matching the keyword.
    Path(tmp_path / "templates" / "Report_Connected_AP_Huawei_v2.xlsx").write_bytes(
        (tmp_path / "templates" / "Report_Connected_AP_Huawei.xlsx").read_bytes()
    )

    with pytest.raises(TemplateError, match="ambiguous template matching 'connected'"):
        resolve_template(tmp_path / "templates", "connected")


def test_resolve_template_single_match_still_resolves(tmp_path):
    templates = build_templates(tmp_path / "templates")

    resolved = resolve_template(tmp_path / "templates", "graph")

    assert resolved == templates["graph"]


def test_resolve_template_missing_reports_placement(tmp_path):
    (tmp_path / "templates").mkdir()

    with pytest.raises(TemplateError, match="no template matching"):
        resolve_template(tmp_path / "templates", "connected")


def test_ensure_day_tab_positions_before_monthly():
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "01"
    wb.create_sheet(title="30")
    wb.create_sheet(title="Monthly")

    assert wb.sheetnames == ["01", "30", "Monthly"]

    ensure_day_tab(wb, "31")

    assert wb.sheetnames == ["01", "30", "31", "Monthly"]

