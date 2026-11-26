from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FEATURE = ROOT / "apps" / "admin" / "src" / "features" / "aml_cft"


def test_ui_exports_required_screens_and_all_state_labels():
    index = (FEATURE / "index.ts").read_text(encoding="utf-8")
    assert all(name in index for name in ("AmlCaseList", "AmlCaseDetail", "ClearanceStatus"))
    types = (FEATURE / "types.ts").read_text(encoding="utf-8")
    for state in ("loading", "empty", "denied", "stale", "conflict", "processing", "error", "ready"):
        assert f'kind: "{state}"' in types


def test_restricted_workbench_enforces_maker_checker_and_server_decided_actions():
    source = (FEATURE / "AmlWorkbench.tsx").read_text(encoding="utf-8")
    assert "不可同一人完成受限例外製作與核定" in source
    assert "allowed_actions" in source
    assert 'isAllowed(item.allowed_actions, "decide_aml_case")' in source
    assert "禁止全平台搜尋索引" in source


def test_general_clearance_widget_never_mentions_restricted_case_detail():
    source = (FEATURE / "ClearanceStatus.tsx").read_text(encoding="utf-8")
    lowered = source.lower()
    for forbidden in ("screening", "evidence", "case_id", "restricted_evidence"):
        assert forbidden not in lowered
    assert "不公開內部案件詳細原因" in source


def test_ui_has_keyboard_focus_and_non_color_status_text():
    css = (FEATURE / "styles.css").read_text(encoding="utf-8")
    workbench = (FEATURE / "AmlWorkbench.tsx").read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert "目前階段" in workbench
    assert 'role="status"' in workbench
