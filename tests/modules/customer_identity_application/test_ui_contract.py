from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FEATURE = ROOT / "apps" / "admin" / "src" / "features" / "customer_identity_application"


def test_ui_exports_required_screens_and_all_state_labels():
    index = (FEATURE / "index.ts").read_text(encoding="utf-8")
    assert all(name in index for name in ("CustomerSearch", "Customer360", "ApplicationTimeline", "MergeReview"))
    types = (FEATURE / "types.ts").read_text(encoding="utf-8")
    for state in ("loading", "empty", "denied", "stale", "conflict", "processing", "error", "ready"):
        assert f'kind: "{state}"' in types


def test_ui_does_not_treat_degraded_source_as_empty():
    source = (FEATURE / "Customer360.tsx").read_text(encoding="utf-8")
    assert "不可解讀為零筆資料" in source
    assert 'source.source_status === "available"' in source
    assert "allowed_actions" in source


def test_ui_has_keyboard_focus_and_non_color_status_text():
    css = (FEATURE / "styles.css").read_text(encoding="utf-8")
    timeline = (FEATURE / "ApplicationTimeline.tsx").read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert "目前階段" in timeline
    assert 'role="status"' in timeline
