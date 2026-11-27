from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from time import perf_counter
from typing import Any, Callable

from .domain import ActorContext, DomainError, ValidationError, new_id
from .service import ReportSignatureService, ServiceResponse
from .telemetry import M07Telemetry


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


_IDEMPOTENT_OPERATIONS = {
    "create_report", "create_report_version", "record_report_review", "freeze_report_version",
    "request_analyst_signature", "receive_analyst_signature", "revoke_signature",
}


class M07Controller:
    """Framework-neutral HTTP adapter for all published M07 operationIds."""

    def __init__(self, service: ReportSignatureService, telemetry: M07Telemetry | None = None) -> None:
        self.service = service
        self.telemetry = telemetry or M07Telemetry()

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
            if operation_id in _IDEMPOTENT_OPERATIONS:
                response_headers["Idempotency-Replayed"] = str(response.idempotency_replayed).lower()
            result = ApiResponse(response.status, response.data, response_headers)
            self.telemetry.observe_request(operation_id, "success", perf_counter() - started)
            # receive_analyst_signature never raises for a mismatched/invalid callback — it
            # persists a 202 "quarantined" receipt instead (UAT-M07-009 requires the frozen
            # package to survive, not a 4xx) — so the alert for a quarantined callback has to
            # fire from this success path, not the DomainError branch below.
            if operation_id == "receive_analyst_signature" and response.data.get("status") == "quarantined":
                self.telemetry.alert("receive_analyst_signature_quarantined")
            return result
        except DomainError as exc:
            field_errors = []
            if exc.field_name:
                field_errors.append({"field": exc.field_name, "code": exc.code, "message": exc.detail})
            problem = {
                "type": f"https://problems.example.invalid/m07/{exc.code}",
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
                exc.status, problem, {"X-Correlation-Id": correlation_id, "Content-Type": "application/problem+json"},
            )
            self.telemetry.observe_request(operation_id, exc.code, perf_counter() - started)
            if exc.status == 503:
                self.telemetry.dependency_failure("signature_provider")
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
        if operation_id == "create_report":
            return self.service.create_report(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "create_report_version":
            return self.service.create_report_version(context, path["report_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "get_report_version":
            return self.service.get_report_version(context, path["report_version_id"], correlation_id=correlation_id)
        if operation_id == "record_report_review":
            return self.service.record_report_review(context, path["report_version_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "freeze_report_version":
            return self.service.freeze_report_version(context, path["report_version_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "request_analyst_signature":
            return self.service.request_analyst_signature(context, path["report_version_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "receive_analyst_signature":
            return self.service.receive_analyst_signature(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "revoke_signature":
            return self.service.revoke_signature(context, path["signature_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        raise ValidationError("unknown operation_id", code="operation_not_found")

    @staticmethod
    def _if_match(value: str | None) -> int | None:
        if value is None:
            return None
        if len(value) < 3 or not value.startswith('"') or not value.endswith('"') or not value[1:-1].isdigit() or int(value[1:-1]) < 1:
            raise ValidationError("If-Match must be a strong integer ETag", field_name="If-Match")
        return int(value[1:-1])


ROUTES: tuple[tuple[str, str, str], ...] = (
    ("POST", "/reports", "create_report"),
    ("POST", "/reports/{report_id}/versions", "create_report_version"),
    ("GET", "/report-versions/{report_version_id}", "get_report_version"),
    ("POST", "/report-versions/{report_version_id}/reviews", "record_report_review"),
    ("POST", "/report-versions/{report_version_id}/freeze", "freeze_report_version"),
    ("POST", "/report-versions/{report_version_id}/signature", "request_analyst_signature"),
    ("POST", "/webhooks/analyst-signatures", "receive_analyst_signature"),
    ("POST", "/signatures/{signature_id}/revocation", "revoke_signature"),
)


def create_fastapi_router(
    service: ReportSignatureService,
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

    controller = M07Controller(service)
    router = APIRouter(tags=["M07"])

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
                operation_id, context, body=request_body, path=dict(request.path_params),
                query=dict(request.query_params), headers=dict(request.headers),
            )
            return JSONResponse(result.body, status_code=result.status, headers=result.headers)

        endpoint.__name__ = operation_id
        endpoint.__annotations__ = {"request": Request, "return": JSONResponse}
        return endpoint

    for method, path, operation_id in ROUTES:
        router.add_api_route(path, endpoint_for(operation_id, method), methods=[method], operation_id=operation_id)
    return router
