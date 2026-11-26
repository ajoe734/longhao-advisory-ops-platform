"""F11 regression: ``plugin.py``'s ``context_resolver`` wired into the real
``longhao_platform.app.PlatformRuntime`` + ``longhao_platform.http.create_app()``.

``plugin.py`` previously mapped every non-service principal to
``principal_type="staff"`` and never set ``customer_id``, so every
authenticated member (``actor_type="customer"``) got 403
``member_session_required`` from the 4 ``/api/v1/member/*`` routes. These
tests exercise the full platform composition (module discovery -> FastAPI
router -> ``M09Controller`` -> ``LegendflowDeliveryService``), not just the
module's own fakes, so a regression in ``plugin.py`` itself is caught.
"""
from __future__ import annotations

from pathlib import Path
import sys
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from longhao_platform.app import PlatformRuntime
from longhao_platform.contracts import PlatformConfig
from longhao_platform.http import create_app
from longhao_platform.types import PlatformError, Principal, Request


TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
MEMBER_ID = UUID("20000000-0000-0000-0000-000000000001")
STAFF_ID = UUID("20000000-0000-0000-0000-000000000003")


class _StubAuthenticator:
    """Exact bearer-token lookup, matching SyntheticAuthenticator's contract
    (claims are never decoded from arbitrary client-supplied text)."""

    def __init__(self) -> None:
        self._principals = {
            "Bearer member-token": Principal(
                MEMBER_ID, TENANT_ID, "member@synthetic.invalid", frozenset({"member"}), actor_type="customer",
            ),
            "Bearer staff-token": Principal(
                STAFF_ID, TENANT_ID, "staff@synthetic.invalid", frozenset({"service_agent"}), actor_type="human",
            ),
        }

    def authenticate(self, request: Request) -> Principal:
        principal = self._principals.get(request.headers.get("authorization", ""))
        if principal is None:
            raise PlatformError(401, "AUTHENTICATION_REQUIRED")
        return principal


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    runtime = PlatformRuntime.bootstrap(
        PlatformConfig(environment="test", synthetic_providers=True, allow_missing_modules=True),
        authenticator=_StubAuthenticator(),
    )
    m09_status = next(row.status for row in runtime.module_statuses if row.module_id == "M09")
    assert m09_status == "loaded"
    return TestClient(create_app(runtime))


def test_member_route_resolves_a_customer_principal_with_member_role(client):
    response = client.get("/api/v1/member/feed", headers={"authorization": "Bearer member-token"})
    assert response.status_code == 200


def test_member_route_rejects_a_staff_principal(client):
    response = client.get("/api/v1/member/feed", headers={"authorization": "Bearer staff-token"})
    assert response.status_code == 403
    assert response.json()["code"] == "member_session_required"


def test_staff_route_resolves_a_staff_principal_with_a_published_role(client):
    response = client.get("/api/v1/deliveries", headers={"authorization": "Bearer staff-token"})
    assert response.status_code == 200


def test_staff_route_rejects_a_member_principal(client):
    response = client.get("/api/v1/deliveries", headers={"authorization": "Bearer member-token"})
    assert response.status_code == 403


def test_m09_routes_are_mounted_exactly_once_under_api_v1(client):
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    paths = spec.json()["paths"]
    assert "/api/v1/member/feed" in paths
    assert "/api/v1/api/v1/member/feed" not in paths


def test_production_configuration_reports_m09_broken_without_a_real_authenticator():
    production_runtime = PlatformRuntime.bootstrap(
        PlatformConfig(environment="production", synthetic_providers=False, allow_missing_modules=False),
        authenticator=_StubAuthenticator(),
    )
    m09_status = next(row for row in production_runtime.module_statuses if row.module_id == "M09")
    assert m09_status.status == "broken"
    assert m09_status.error_code == "MODULE_REGISTRATION_FAILED"
