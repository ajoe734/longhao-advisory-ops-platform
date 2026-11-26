"""Static text contract checks for apps/admin/src/features/ownership_pool/.

This VM has no `node_modules` and is not authorized to install one (see
traceability.md), so there is no `tsc`/test-runner available to exercise the
React components behaviorally. These checks instead read the TSX/CSS source
as text and assert the required ViewState kinds (docs/modules/M11-ownership_pool.md
§10: "共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、...")
are actually handled by each screen, not merely declared in the ViewState type.
A behavioral render/interaction test still requires a real browser/test runner
and stays NOT_RUN here.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FEATURE_DIR = ROOT / "apps" / "admin" / "src" / "features" / "ownership_pool"

_REQUIRED_KINDS = ("loading", "empty", "denied", "stale", "conflict", "processing", "error")


def _read(name: str) -> str:
    path = FEATURE_DIR / name
    assert path.is_file(), f"expected {path} to exist"
    return path.read_text(encoding="utf-8")


def test_view_state_type_declares_every_required_kind():
    types_src = _read("types.ts")
    for kind in _REQUIRED_KINDS:
        assert f'kind: "{kind}"' in types_src, f"ViewState is missing the {kind!r} variant"
    assert '"ready"' in types_src


def test_company_pool_workbench_handles_stale_and_conflict_banners():
    src = _read("CompanyPoolWorkbench.tsx")
    for kind in _REQUIRED_KINDS:
        assert f'state.kind === "{kind}"' in src, f"CompanyPoolWorkbench never checks state.kind === {kind!r}"
    # The stale/conflict banners must come with a way back to fresh data, not
    # just an inert message.
    assert "onReload" in src


def test_maintenance_timeline_handles_stale_and_conflict_banners():
    src = _read("MaintenanceTimeline.tsx")
    for kind in _REQUIRED_KINDS:
        assert f'state.kind === "{kind}"' in src, f"MaintenanceTimeline never checks state.kind === {kind!r}"
    assert "onReload" in src


def test_policy_preview_result_handles_stale_and_conflict():
    src = _read("PolicyPreview.tsx")
    for kind in _REQUIRED_KINDS:
        assert f'state.kind === "{kind}"' in src, f"PolicyPreview's Result() never checks state.kind === {kind!r}"


def test_stale_banner_style_exists():
    css = _read("styles.css")
    assert "m11-banner-stale" in css
