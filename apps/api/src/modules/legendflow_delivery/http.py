from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import inspect
from time import perf_counter
from typing import Any, Callable

from .domain import ActorContext, DomainError, ValidationError, new_id
from .service import LegendflowDeliveryService, ServiceResponse
from .telemetry import M09Telemetry


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


#: operations whose success response must echo whether the Idempotency-Key
#: request was replayed rather than newly applied.
_IDEMPOTENT_OPERATIONS = {
    "bind_member_channel",
    "set_communication_preference",
    "receive_message_receipt",
    "reconcile_delivery",
    "redrive_delivery_event",
}


class M09Controller:
    """Framework-neutral HTTP adapter for all published M09 operationIds."""

    def __init__(self, service: LegendflowDeliveryService, telemetry: M09Telemetry | None = None) -> None:
        self.service = service
        self.telemetry = telemetry or M09Telemetry()

    def handle(
        self,
        operation_id: str,
        context: ActorContext | None,
        *,
        body: dict[str, Any] | None = None,
        path: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        raw_body: bytes = b"",
        method: str = "GET",
        request_path: str = "",
    ) -> ApiResponse:
        body = body or {}
        path = path or {}
        query = query or {}
        headers = {key.lower(): value for key, value in (headers or {}).items()}
        correlation_id = headers.get("x-correlation-id") or new_id()
        started = perf_counter()
        try:
            response = self._dispatch(operation_id, context, body, path, query, headers, correlation_id, raw_body, method, request_path)
            response_headers = {"X-Correlation-Id": correlation_id}
            if response.etag:
                response_headers["ETag"] = response.etag
            if operation_id in _IDEMPOTENT_OPERATIONS:
                response_headers["Idempotency-Replayed"] = str(response.idempotency_replayed).lower()
            result = ApiResponse(response.status, response.data, response_headers)
            self.telemetry.observe_request(operation_id, "success", perf_counter() - started)
            return result
        except DomainError as exc:
            field_errors = []
            if exc.field_name:
                field_errors.append({"field": exc.field_name, "code": exc.code, "message": exc.detail})
            problem = {
                "type": f"https://problems.example.invalid/m09/{exc.code}",
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
                exc.status, problem, {"X-Correlation-Id": correlation_id, "Content-Type": "application/problem+json"}
            )
            self.telemetry.observe_request(operation_id, exc.code, perf_counter() - started)
            if exc.status == 503:
                self.telemetry.dependency_failure("legendflow_delivery_upstream")
            return result

    def _dispatch(
        self,
        operation_id: str,
        context: ActorContext | None,
        body: dict[str, Any],
        path: dict[str, str],
        query: dict[str, Any],
        headers: dict[str, str],
        correlation_id: str,
        raw_body: bytes,
        method: str,
        request_path: str,
    ) -> ServiceResponse:
        key = headers.get("idempotency-key", "")
        version = self._if_match(headers.get("if-match"))
        if operation_id == "get_member_feed":
            return self.service.get_member_feed(
                context, state_filter=query.get("state"), cursor=query.get("cursor"),
                limit=self._limit(query.get("limit")), as_of=self._as_of(query.get("as_of")), correlation_id=correlation_id,
            )
        if operation_id == "request_member_report_access":
            return self.service.request_member_report_access(context, path["report_version_id"], correlation_id=correlation_id)
        if operation_id == "bind_member_channel":
            return self.service.bind_member_channel(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "set_communication_preference":
            return self.service.set_communication_preference(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "list_deliveries":
            return self.service.list_deliveries(
                context, state_filter=query.get("state"), cursor=query.get("cursor"),
                limit=self._limit(query.get("limit")), as_of=self._as_of(query.get("as_of")), correlation_id=correlation_id,
            )
        if operation_id == "receive_message_receipt":
            return self.service.receive_message_receipt(
                body, idempotency_key=key, key_id=headers.get("x-integration-key-id", ""),
                timestamp=headers.get("x-webhook-timestamp", ""), nonce=headers.get("x-webhook-nonce", ""),
                signature=headers.get("x-webhook-signature", ""), method=method, path=request_path,
                raw_body=raw_body, correlation_id=correlation_id,
            )
        if operation_id == "reconcile_delivery":
            return self.service.reconcile_delivery(
                context, path["delivery_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id
            )
        if operation_id == "redrive_delivery_event":
            return self.service.redrive_delivery_event(
                context, path["dead_letter_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id
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
    ("GET", "/member/feed", "get_member_feed"),
    ("GET", "/member/reports/{report_version_id}/access", "request_member_report_access"),
    ("POST", "/member/channel-bindings", "bind_member_channel"),
    ("POST", "/member/communication-preferences", "set_communication_preference"),
    ("GET", "/deliveries", "list_deliveries"),
    ("POST", "/webhooks/message-receipts", "receive_message_receipt"),
    ("POST", "/deliveries/{delivery_id}/reconciliation", "reconcile_delivery"),
    ("POST", "/dead-letters/{dead_letter_id}/redrive", "redrive_delivery_event"),
)

#: these operationIds authenticate via the webhook signature, not a bearer principal.
_UNAUTHENTICATED_ROUTES = {"receive_message_receipt"}


def create_fastapi_router(
    service: LegendflowDeliveryService,
    context_resolver: Callable[[Any], ActorContext],
) -> Any:
    """Build the integration router without forcing FastAPI into this module's lockfile.

    The build steward owns shared dependencies. Importing this module and running
    domain tests therefore remains possible before FastAPI is installed.
    """

    try:
        from fastapi import APIRouter, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover - exercised by integration composition
        raise RuntimeError("FastAPI must be supplied by the shared application build") from exc

    controller = M09Controller(service)
    # No prefix here: longhao_platform.http.create_app() mounts every module
    # router with app.include_router(router, prefix="/api/v1") itself, so a
    # prefix on this router would double up into "/api/v1/api/v1/...".
    router = APIRouter(tags=["M09"])

    def endpoint_for(operation_id: str, method: str) -> Callable[..., Any]:
        async def endpoint(request: Request) -> JSONResponse:
            context = None
            if operation_id not in _UNAUTHENTICATED_ROUTES:
                context = context_resolver(request)
                if inspect.isawaitable(context):
                    context = await context
            raw_body = b""
            request_body: dict[str, Any] = {}
            if method not in {"GET", "HEAD"}:
                raw_body = await request.body()
                try:
                    request_body = await request.json()
                except Exception:  # FastAPI/Starlette raises different decode errors by version.
                    request_body = {}
            result = controller.handle(
                operation_id, context, body=request_body, path=dict(request.path_params),
                query=dict(request.query_params), headers=dict(request.headers),
                raw_body=raw_body, method=method, request_path=request.url.path,
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
