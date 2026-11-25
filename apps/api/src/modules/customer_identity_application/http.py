from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import inspect
import re
from time import perf_counter
from typing import Any, Callable

from .domain import ActorContext, DomainError, ValidationError, new_id
from .service import CustomerIdentityApplicationService, ServiceResponse
from .telemetry import M01Telemetry


_IF_MATCH_RE = re.compile(r'^"[1-9][0-9]*"$')


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


class M01Controller:
    """Framework-neutral HTTP adapter for all published M01 operationIds."""

    def __init__(self, service: CustomerIdentityApplicationService, telemetry: M01Telemetry | None = None) -> None:
        self.service = service
        self.telemetry = telemetry or M01Telemetry()

    def handle(
        self,
        operation_id: str,
        context: ActorContext,
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
            response = self._dispatch(operation_id, context, body, path, query, headers, correlation_id)
            response_headers = {"X-Correlation-Id": correlation_id}
            if response.etag:
                response_headers["ETag"] = response.etag
            if operation_id in {
                "create_customer",
                "update_customer",
                "link_external_identity",
                "propose_customer_merge",
                "decide_customer_merge",
                "create_application",
                "withdraw_application",
                "record_consent",
            }:
                response_headers["Idempotency-Replayed"] = str(response.idempotency_replayed).lower()
            result = ApiResponse(response.status, response.data, response_headers)
            self.telemetry.observe_request(operation_id, "success", perf_counter() - started)
            return result
        except DomainError as exc:
            field_errors = []
            if exc.field_name:
                field_errors.append({"field": exc.field_name, "code": exc.code, "message": exc.detail})
            problem = {
                "type": f"https://problems.example.invalid/m01/{exc.code}",
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
            result = ApiResponse(
                exc.status,
                problem,
                {"X-Correlation-Id": correlation_id, "Content-Type": "application/problem+json"},
            )
            self.telemetry.observe_request(operation_id, exc.code, perf_counter() - started)
            if exc.status == 503:
                self.telemetry.dependency_failure("identity_provider")
            return result

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
        if operation_id == "create_customer":
            return self.service.create_customer(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "list_customers":
            return self.service.list_customers(
                context,
                state_filter=query.get("state"),
                cursor=query.get("cursor"),
                limit=self._limit(query.get("limit")),
                as_of=self._as_of(query.get("as_of")),
                correlation_id=correlation_id,
            )
        if operation_id == "get_customer":
            return self.service.get_customer(context, path["customer_id"], correlation_id=correlation_id)
        if operation_id == "update_customer":
            return self.service.update_customer(context, path["customer_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "link_external_identity":
            return self.service.link_external_identity(context, path["customer_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "propose_customer_merge":
            return self.service.propose_customer_merge(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "list_customer_merges":
            return self.service.list_customer_merges(
                context,
                state_filter=query.get("state"),
                cursor=query.get("cursor"),
                limit=self._limit(query.get("limit")),
                as_of=self._as_of(query.get("as_of")),
                correlation_id=correlation_id,
            )
        if operation_id == "get_customer_merge":
            return self.service.get_customer_merge(context, path["merge_case_id"], correlation_id=correlation_id)
        if operation_id == "decide_customer_merge":
            return self.service.decide_customer_merge(context, path["merge_case_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "create_application":
            return self.service.create_application(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "get_application":
            return self.service.get_application(context, path["application_id"], correlation_id=correlation_id)
        if operation_id == "withdraw_application":
            return self.service.withdraw_application(context, path["application_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "record_consent":
            return self.service.record_consent(context, path["customer_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        raise ValidationError("unknown operation_id", code="operation_not_found")

    @staticmethod
    def _if_match(value: str | None) -> int | None:
        if value is None:
            return None
        if not _IF_MATCH_RE.fullmatch(value):
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
    ("POST", "/customers", "create_customer"),
    ("GET", "/customers", "list_customers"),
    ("GET", "/customers/{customer_id}", "get_customer"),
    ("PATCH", "/customers/{customer_id}", "update_customer"),
    ("POST", "/customers/{customer_id}/identity-links", "link_external_identity"),
    ("POST", "/customer-merges", "propose_customer_merge"),
    ("GET", "/customer-merges", "list_customer_merges"),
    ("GET", "/customer-merges/{merge_case_id}", "get_customer_merge"),
    ("POST", "/customer-merges/{merge_case_id}/decision", "decide_customer_merge"),
    ("POST", "/applications", "create_application"),
    ("GET", "/applications/{application_id}", "get_application"),
    ("POST", "/applications/{application_id}/withdrawal", "withdraw_application"),
    ("POST", "/customers/{customer_id}/consents", "record_consent"),
)


def create_fastapi_router(
    service: CustomerIdentityApplicationService,
    context_resolver: Callable[[Any], ActorContext],
    *,
    prefix: str = "",
) -> Any:
    """Build the integration router without forcing FastAPI into this module's lockfile.

    The build steward owns shared dependencies. Importing this module and running
    domain tests therefore remains possible before FastAPI is installed. `prefix`
    defaults to empty because `longhao_platform/http.py` mounts module routers with
    `app.include_router(router, prefix="/api/v1")`; a non-empty default here would
    double the `/api/v1` segment once wired into `plugin.py`.
    """

    try:
        from fastapi import APIRouter, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover - exercised by integration composition
        raise RuntimeError("FastAPI must be supplied by the shared application build") from exc

    controller = M01Controller(service)
    router = APIRouter(prefix=prefix, tags=["M01"])

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
        # Imports are intentionally local so module tests do not own the shared
        # FastAPI dependency. Replace postponed strings with the real classes
        # before FastAPI inspects the callable signature.
        endpoint.__annotations__ = {"request": Request, "return": JSONResponse}
        return endpoint

    for method, path, operation_id in ROUTES:
        router.add_api_route(path, endpoint_for(operation_id, method), methods=[method], operation_id=operation_id)
    return router
