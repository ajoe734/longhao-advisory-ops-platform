"""The composed platform must load all fourteen modules and expose their routes.

Module task boards and module-local tests can all be green while nothing is
reachable through the shared app (missing ``plugin.py`` or a router that adds
its own ``/api/v1`` on top of the platform mount). This guards the composition.
"""
from __future__ import annotations

import collections

import pytest
from fastapi.testclient import TestClient

from longhao_platform.app import PlatformRuntime
from longhao_platform.http import create_app

MODULE_IDS = [f"M{number:02d}" for number in range(1, 15)]


@pytest.fixture(scope="module")
def runtime() -> PlatformRuntime:
    return PlatformRuntime.bootstrap()


@pytest.fixture(scope="module")
def operations(runtime):
    spec = create_app(runtime).openapi()
    return [
        (method.upper(), path, (operation.get("tags") or [None])[0])
        for path, item in spec["paths"].items()
        for method, operation in item.items()
    ]


def test_all_fourteen_modules_are_loaded(runtime):
    statuses = {status.module_id: (status.status, status.error_code) for status in runtime.module_statuses}
    assert statuses == {module_id: ("loaded", None) for module_id in MODULE_IDS}
    status, body = runtime.readiness(disclose_modules=True)
    assert status == 200
    assert body["status"] == "ready"
    assert body["release_gate_passed"] is True


def test_every_module_exposes_routes(operations):
    per_module = collections.Counter(tag for _method, _path, tag in operations)
    assert {module_id for module_id in MODULE_IDS if per_module[module_id] == 0} == set()


def test_routes_are_mounted_once_under_api_v1(operations):
    module_paths = [path for _method, path, tag in operations if tag in MODULE_IDS]
    assert [path for path in module_paths if not path.startswith("/api/v1/")] == []
    assert [path for path in module_paths if path.startswith("/api/v1/api/v1")] == []
    counts = collections.Counter((method, path) for method, path, _tag in operations)
    assert [key for key, count in counts.items() if count > 1] == []


def test_module_routes_require_authentication(runtime, operations):
    client = TestClient(create_app(runtime))
    checked = set()
    for method, path, tag in operations:
        if tag in MODULE_IDS and method == "GET" and "{" not in path and tag not in checked:
            response = client.get(path)
            assert response.status_code == 401, (tag, path, response.status_code)
            checked.add(tag)
    assert checked, "no parameterless GET route found to probe"
