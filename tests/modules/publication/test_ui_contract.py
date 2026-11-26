from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FEATURE = ROOT / "apps" / "admin" / "src" / "features" / "publication"


def test_ui_exports_required_screens_and_all_state_labels():
    index = (FEATURE / "index.ts").read_text(encoding="utf-8")
    assert all(name in index for name in ("RecommendationSeriesView", "PublicationMonitor", "CorrectionWorkspace"))
    types = (FEATURE / "types.ts").read_text(encoding="utf-8")
    for state in ("loading", "empty", "denied", "stale", "conflict", "processing", "error", "ready"):
        assert f'kind: "{state}"' in types


def test_correction_workspace_binds_the_typed_id_to_the_looked_up_replacement():
    source = (FEATURE / "CorrectionWorkspace.tsx").read_text(encoding="utf-8")
    # The id actually submitted must come from the looked-up replacement
    # object, never the raw (possibly stale/edited) text field, and the
    # submit button must stay disabled unless they still agree.
    assert "replacementMatchesInput" in source
    assert "onSubmit(replacementPublication.id, replacementPublication.report_version_id" in source
    assert "disabled={!canCorrect || !replacementMatchesInput || !reasonCode.trim()}" in source


def test_correction_workspace_renders_conflict_and_stale_banners_with_reload():
    source = (FEATURE / "CorrectionWorkspace.tsx").read_text(encoding="utf-8")
    assert 'original.kind === "conflict"' in source
    assert 'original.kind === "stale"' in source
    assert "onReload" in source


def test_ui_has_keyboard_focus_visible_styles():
    css = (FEATURE / "styles.css").read_text(encoding="utf-8")
    assert ":focus-visible" in css
