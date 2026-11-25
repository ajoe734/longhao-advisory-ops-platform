from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_admin_registry_covers_all_contracted_screens_once():
    screens = json.loads((ROOT / "apps/admin/src/registry.json").read_text(encoding="utf-8"))
    expected_ids = [f"UI-{number:02d}" for number in range(1, 35)]
    assert [screen["id"] for screen in screens] == expected_ids
    assert len({screen["path"] for screen in screens}) == 34
    assert {screen["moduleId"] for screen in screens} == {
        "M01", "M02", "M03", "M04", "M05", "M06", "M07",
        "M08", "M09", "M10", "M11", "M12", "M13", "M14", "SHARED",
    }
    assert all(screen["path"].startswith("/") for screen in screens)


def test_admin_dependencies_are_exactly_pinned():
    package = json.loads((ROOT / "apps/admin/package.json").read_text(encoding="utf-8"))
    versions = {**package["dependencies"], **package["devDependencies"]}
    assert versions
    assert all(version[0].isdigit() and " " not in version for version in versions.values())


def test_admin_css_uses_the_published_design_tokens():
    tokens = json.loads((ROOT / "design/tokens.json").read_text(encoding="utf-8"))
    css = (ROOT / "apps/admin/src/styles.css").read_text(encoding="utf-8")
    variables = dict(re.findall(r"--color-([a-z]+):\s*(#[0-9A-Fa-f]{6})", css))
    assert {key: value.upper() for key, value in variables.items()} == {
        key: value.upper() for key, value in tokens["colors"].items()
    }
    assert f'--focus-color: {tokens["focus_ring"]["color"]}' in css
    assert f'--focus-width: {tokens["focus_ring"]["width_px"]}px' in css
