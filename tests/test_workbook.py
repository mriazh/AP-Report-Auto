from __future__ import annotations

from pathlib import Path

import pytest
from fixtures import build_templates
from huawei_ap_report.errors import TemplateError
from huawei_ap_report.workbook import resolve_template


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
