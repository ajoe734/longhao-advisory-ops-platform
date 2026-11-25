from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FEATURE = ROOT / "apps" / "admin" / "src" / "features" / "agora_review"


def test_ui_exports_required_screens_and_all_state_labels():
    index = (FEATURE / "index.ts").read_text(encoding="utf-8")
    assert all(name in index for name in ("ReviewQueue", "ReviewCaseWorkspace", "RevisionRequestForm", "RevisionCompare"))
    types = (FEATURE / "types.ts").read_text(encoding="utf-8")
    for state in ("loading", "empty", "denied", "stale", "conflict", "processing", "error", "ready"):
        assert f'kind: "{state}"' in types


def test_ui_does_not_treat_degraded_servant_as_empty_or_completed():
    workspace = (FEATURE / "ReviewCaseWorkspace.tsx").read_text(encoding="utf-8")
    assert "不代表無修訂或已完成" in workspace
    assert "不可解讀為零筆資料" in workspace
    assert 'model.servant_health.status === "available"' in workspace
    assert "allowed_actions" in workspace


def test_ui_marks_stale_case_without_silently_approving_new_version():
    workspace = (FEATURE / "ReviewCaseWorkspace.tsx").read_text(encoding="utf-8")
    assert "source_version_stale" in workspace
    assert "不會自動沿用為新版本的核准" in workspace
    compare = (FEATURE / "RevisionCompare.tsx").read_text(encoding="utf-8")
    assert "不會自動視為新版覆核" in compare
    assert "不會默默替換既有預覽內容" in compare


def test_ui_has_keyboard_focus_and_non_color_status_text():
    css = (FEATURE / "styles.css").read_text(encoding="utf-8")
    workspace = (FEATURE / "ReviewCaseWorkspace.tsx").read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert "目前階段" in workspace
    assert 'role="status"' in workspace
