from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import inspect
from time import perf_counter
from typing import Any, Callable

from .domain import ActorContext, DomainError, UnauthorizedError, ValidationError, new_id
from .service import OwnershipPoolService, ServiceResponse
from .telemetry import M11Telemetry


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


_IDEMPOTENT_WRITE_OPS = {
    "create_company_pool_policy",
    "approve_company_pool_policy",
    "assign_maintenance_owner",
    "claim_pool_customer",
    "release_maintenance_owner",
    "record_employee_departure",
    "evaluate_company_pool",
}


class M11Controller:
    """Framework-neutral HTTP adapter for the published M11 operationIds."""

    def __init__(self, service: OwnershipPoolService, telemetry: M11Telemetry | None = None) -> None:
        self.service = service
        self.telemetry = telemetry or M11Telemetry()

    def handle(
        self,
        operation_id: str,
        context: ActorContext | None,
        *,
        body: dict[str, Any] | None = None,
        path: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> ApiResponse:
        body = body or {}
        path = path or {}
        query = query or {}
        headers = {key.lower(): value for key, value in (headers or {}).items()}
        correlation_id = headers.get("x-correlation-id") or new_id()
        started = perf_counter()
        try:
            if context is None:
                raise UnauthorizedError("an authenticated actor context is required")
            response = self._dispatch(operation_id, context, body, path, query, headers, correlation_id)
            response_headers = {"X-Correlation-Id": correlation_id}
            if response.etag:
                response_headers["ETag"] = response.etag
            if operation_id in _IDEMPOTENT_WRITE_OPS:
                response_headers["Idempotency-Replayed"] = str(response.idempotency_replayed).lower()
            self.telemetry.observe_request(operation_id, "success", perf_counter() - started)
            return ApiResponse(response.status, response.data, response_headers)
        except DomainError as exc:
            field_errors = []
            if exc.field_name:
                field_errors.append({"field": exc.field_name, "code": exc.code, "message": exc.detail})
            problem = {
                "type": f"https://problems.example.invalid/m11/{exc.code}",
                "title": exc.code.replace("_", " ").title(),
                "status": exc.status,
                "detail": exc.detail,
                "instance": f"urn:longhao:correlation:{correlation_id}",
                "code": exc.code,
                "correlation_id": correlation_id,
                "retryable": exc.retryable,
                "field_errors": field_errors,
                "current_version": exc.current_version,
            }
            self.telemetry.observe_request(operation_id, exc.code, perf_counter() - started)
            if exc.status == 503:
                self.telemetry.dependency_failure("ownership_dependency")
            return ApiResponse(
                exc.status, problem, {"X-Correlation-Id": correlation_id, "Content-Type": "application/problem+json"}
            )

    def _dispatch(
        self,
        operation_id: str,
        context: ActorContext,
        body: dict[str, Any],
        path: dict[str, str],
        query: dict[str, Any],
        headers: dict[str, str],
        correlation_id: str,
    ) -> ServiceResponse:
        key = headers.get("idempotency-key", "")
        version = self._if_match(headers.get("if-match"))
        if operation_id == "create_company_pool_policy":
            return self.service.create_company_pool_policy(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "approve_company_pool_policy":
            return self.service.approve_company_pool_policy(
                context,
                path["policy_id"],
                body,
                expected_version=version,
                idempotency_key=key,
                correlation_id=correlation_id,
            )
        if operation_id == "get_current_company_pool_policy":
            return self.service.get_current_company_pool_policy(context, correlation_id=correlation_id)
        if operation_id == "list_company_pool":
            return self.service.list_company_pool(
                context,
                state_filter=query.get("state"),
                cursor=query.get("cursor"),
                limit=self._limit(query.get("limit")),
                as_of=self._as_of(query.get("as_of")),
                correlation_id=correlation_id,
            )
        if operation_id == "assign_maintenance_owner":
            return self.service.assign_maintenance_owner(
                context, path["customer_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id
            )
        if operation_id == "claim_pool_customer":
            return self.service.claim_pool_customer(
                context, path["pool_entry_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id
            )
        if operation_id == "release_maintenance_owner":
            return self.service.release_maintenance_owner(
                context, path["assignment_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id
            )
        if operation_id == "record_employee_departure":
            return self.service.record_employee_departure(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "evaluate_company_pool":
            return self.service.evaluate_company_pool(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "get_ownership_history":
            return self.service.get_ownership_history(
                context,
                path["customer_id"],
                state_filter=query.get("state"),
                cursor=query.get("cursor"),
                limit=self._limit(query.get("limit")),
                as_of=self._as_of(query.get("as_of")),
                correlation_id=correlation_id,
            )
        raise ValidationError("unknown operation_id", code="operation_not_found")

    @staticmethod
    def _if_match(value: str | None) -> int | None:
        if value is None:
            return None
        if len(value) < 3 or not value.startswith('"') or not value.endswith('"') or not value[1:-1].isdigit() or int(value[1:-1]) < 1:
            raise ValidationError("If-Match must be a strong integer ETag", field_name="If-Match")
        return int(value[1:-1])

    @staticmethod
    def _limit(value: Any) -> int:
        try:
            return int(value) if value is not None else 50
        except (TypeError, ValueError) as exc:
            raise ValidationError("limit must be an integer", field_name="limit") from exc

    @staticmethod
    def _as_of(value: str | None) -> datetime | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
            return parsed
        except (ValueError, AttributeError) as exc:
            raise ValidationError("as_of must be an RFC3339 timestamp", field_name="as_of") from exc


ROUTES: tuple[tuple[str, str, str], ...] = (
    ("POST", "/ownership/company-pool-policies", "create_company_pool_policy"),
    ("POST", "/ownership/company-pool-policies/{policy_id}/approval", "approve_company_pool_policy"),
    ("GET", "/ownership/company-pool-policies/current", "get_current_company_pool_policy"),
    ("GET", "/ownership/company-pool", "list_company_pool"),
    ("POST", "/customers/{customer_id}/maintenance-assignments", "assign_maintenance_owner"),
    ("POST", "/ownership/pool-entries/{pool_entry_id}/claim", "claim_pool_customer"),
    ("POST", "/ownership/assignments/{assignment_id}/release", "release_maintenance_owner"),
    ("POST", "/ownership/departures", "record_employee_departure"),
    ("POST", "/ownership/pool-evaluations", "evaluate_company_pool"),
    ("GET", "/customers/{customer_id}/ownership-history", "get_ownership_history"),
)


def create_fastapi_router(service: OwnershipPoolService, context_resolver: Callable[[Any], ActorContext]) -> Any:
    """Build the integration router without forcing FastAPI into this module's lockfile.

    The build steward owns shared dependencies. Importing this module and running
    domain tests therefore remains possible before FastAPI is installed, matching
    modules/customer_identity_application/http.py's precedent.
    """

    try:
        from fastapi import APIRouter, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover - exercised by integration composition
        raise RuntimeError("FastAPI must be supplied by the shared application build") from exc

    controller = M11Controller(service)
    router = APIRouter(prefix="/api/v1", tags=["M11"])

    def endpoint_for(operation_id: str, method: str) -> Callable[..., Any]:
        async def endpoint(request: Request) -> JSONResponse:
            context = context_resolver(request)
            if inspect.isawaitable(context):
                context = await context
            request_body: dict[str, Any] = {}
            if method not in {"GET", "HEAD"}:
                try:
                    request_body = await request.json()
                except Exception:  # FastAPI/Starlette raises different decode errors by version.
                    request_body = {}
            result = controller.handle(
                operation_id,
                context,
                body=request_body,
                path=dict(request.path_params),
                query=dict(request.query_params),
                headers=dict(request.headers),
            )
            return JSONResponse(result.body, status_code=result.status, headers=result.headers)

        endpoint.__name__ = operation_id
        endpoint.__annotations__ = {"request": Request, "return": JSONResponse}
        return endpoint

    for method, path, operation_id in ROUTES:
        router.add_api_route(path, endpoint_for(operation_id, method), methods=[method], operation_id=operation_id)
    return router
