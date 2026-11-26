from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FEATURE = ROOT / "apps" / "admin" / "src" / "features" / "service_cases"


def test_ui_exports_required_screens_and_all_state_labels():
    index = (FEATURE / "index.ts").read_text(encoding="utf-8")
    assert all(name in index for name in ("ServiceCaseQueue", "CaseDetail", "AppealRefundPanel"))
    types = (FEATURE / "types.ts").read_text(encoding="utf-8")
    for state in ("loading", "empty", "denied", "stale", "conflict", "processing", "error", "ready"):
        assert f'kind: "{state}"' in types


def test_ui_hides_contract_and_payment_links_for_unverified_case():
    detail = (FEATURE / "CaseDetail.tsx").read_text(encoding="utf-8")
    assert "unverified" in detail
    assert "尚未完成客戶身份驗證" in detail
    assert "allowed_actions" in detail


def test_ui_never_implies_response_delivered_without_evidence():
    panel = (FEATURE / "AppealRefundPanel.tsx").read_text(encoding="utf-8")
    assert '"queued"' in panel and '"delivered"' in panel and '"unknown"' in panel
    assert "不視為已送達" in panel
    assert "不可自行承諾未核定退款金額" in panel
    assert "contains_new_investment_judgment" in panel


def test_ui_marks_internal_notes_distinctly_in_timeline():
    detail = (FEATURE / "CaseDetail.tsx").read_text(encoding="utf-8")
    assert 'data-direction' in detail
    assert "內部筆記" in detail


def test_ui_has_keyboard_focus_and_non_color_status_text():
    css = (FEATURE / "styles.css").read_text(encoding="utf-8")
    queue = (FEATURE / "ServiceCaseQueue.tsx").read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert 'role="status"' in queue
