from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FEATURE = ROOT / "apps" / "admin" / "src" / "features" / "finance_control"


def test_ui_exports_required_screens_and_all_state_labels():
    index = (FEATURE / "index.ts").read_text(encoding="utf-8")
    assert all(name in index for name in ("FinanceWorkbench", "RefundApproval", "AuditEvidenceWorkbench", "ControlExceptionQueue"))
    types = (FEATURE / "types.ts").read_text(encoding="utf-8")
    for state in ("loading", "empty", "denied", "stale", "conflict", "processing", "error", "ready"):
        assert f'kind: "{state}"' in types


def test_refund_ui_distinguishes_unearned_from_earned_reversal_and_gates_by_allowed_actions():
    source = (FEATURE / "RefundApproval.tsx").read_text(encoding="utf-8")
    assert "unearned_amount" in source and "earned_reversal_amount" in source
    assert "isAllowed" in source
    assert "reconciliation_required" in source  # UAT-M14-006: unknown refund result is surfaced, not hidden


def test_audit_ui_masks_restricted_actions_without_treating_as_empty():
    source = (FEATURE / "AuditEvidenceWorkbench.tsx").read_text(encoding="utf-8")
    assert '"restricted"' in source
    assert "manifest_sha256" in source


def test_control_exception_ui_requires_independent_closure():
    source = (FEATURE / "ControlExceptionQueue.tsx").read_text(encoding="utf-8")
    assert "獨立稽核覆核" in source
    assert "canApproveClosure" in source


def test_ui_has_keyboard_focus_and_non_color_status_text():
    css = (FEATURE / "styles.css").read_text(encoding="utf-8")
    workbench = (FEATURE / "FinanceWorkbench.tsx").read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert 'role="status"' in workbench or 'role="alert"' in workbench
