from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FEATURE = ROOT / "apps" / "admin" / "src" / "features" / "report_signature"


def test_ui_exports_required_screens_and_all_state_labels():
    index = (FEATURE / "index.ts").read_text(encoding="utf-8")
    assert all(name in index for name in ("ReportWorkbench", "SignatureConfirmation", "SignatureEvidence"))
    types = (FEATURE / "types.ts").read_text(encoding="utf-8")
    for state in ("loading", "empty", "denied", "stale", "conflict", "processing", "error", "ready"):
        assert f'kind: "{state}"' in types


def test_uat_m07_011_confirmation_screen_discloses_auto_publish_with_no_manual_gate():
    source = (FEATURE / "SignatureConfirmation.tsx").read_text(encoding="utf-8")
    assert "簽署後自動發布" in source
    assert "確認簽署" in source
    # No separate manual "Publish" action exists after signing in this feature.
    assert "發布按鈕" not in source and "onPublish" not in source and "手動發布" not in source


def test_evidence_screen_does_not_treat_received_callback_as_published():
    source = (FEATURE / "SignatureEvidence.tsx").read_text(encoding="utf-8")
    assert 'version.state === "signed"' in source
    assert "不代表已驗證或已發布" in source
    assert "content_sha256" not in source or "report_content_sha256" in source  # uses the real field name, not an invented one


def test_ui_has_keyboard_focus_and_non_color_status_text():
    css = (FEATURE / "styles.css").read_text(encoding="utf-8")
    workbench = (FEATURE / "ReportWorkbench.tsx").read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert "目前階段" in workbench
    assert 'role="status"' in workbench


def test_signature_confirmation_disables_sign_button_with_explained_reason():
    source = (FEATURE / "SignatureConfirmation.tsx").read_text(encoding="utf-8")
    assert "disabled={!allowed" in source
    assert "missingReason" in source and "title={missingReason}" in source
