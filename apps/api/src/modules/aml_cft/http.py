from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import inspect
from time import perf_counter
from typing import Any, Callable

from .domain import ActorContext, DomainError, ValidationError, new_id
from .service import AmlCftService, ServiceResponse
from .telemetry import M04Telemetry


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


_IDEMPOTENT_COMMANDS = {
    "submit_aml_declaration",
    "start_aml_screening",
    "receive_aml_screening",
    "add_aml_case_action",
    "decide_aml_case",
}


class M04Controller:
    """Framework-neutral HTTP adapter for all published M04 operationIds."""

    def __init__(self, service: AmlCftService, telemetry: M04Telemetry | None = None) -> None:
        self.service = service
        self.telemetry = telemetry or M04Telemetry()

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
            if operation_id in _IDEMPOTENT_COMMANDS:
                response_headers["Idempotency-Replayed"] = str(response.idempotency_replayed).lower()
            result = ApiResponse(response.status, response.data, response_headers)
            self.telemetry.observe_request(operation_id, "success", perf_counter() - started)
            return result
        except DomainError as exc:
            field_errors = []
            if exc.field_name:
                field_errors.append({"field": exc.field_name, "code": exc.code, "message": exc.detail})
            problem = {
                "type": f"https://problems.example.invalid/m04/{exc.code}",
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
                self.telemetry.dependency_failure("screening_provider")
            return result

    def _dispatch(
        self, operation_id: str, context: ActorContext, body: dict[str, Any], path: dict[str, str],
        query: dict[str, Any], headers: dict[str, str], correlation_id: str,
    ) -> ServiceResponse:
        key = headers.get("idempotency-key", "")
        version = self._if_match(headers.get("if-match"))
        if operation_id == "submit_aml_declaration":
            return self.service.submit_aml_declaration(context, path["application_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "start_aml_screening":
            return self.service.start_aml_screening(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "receive_aml_screening":
            return self.service.receive_aml_screening(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "list_aml_cases":
            return self.service.list_aml_cases(
                context, state_filter=query.get("state"), cursor=query.get("cursor"),
                limit=self._limit(query.get("limit")), as_of=self._as_of(query.get("as_of")), correlation_id=correlation_id,
            )
        if operation_id == "get_aml_case":
            return self.service.get_aml_case(context, path["aml_case_id"], correlation_id=correlation_id)
        if operation_id == "add_aml_case_action":
            return self.service.add_aml_case_action(context, path["aml_case_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "decide_aml_case":
            return self.service.decide_aml_case(context, path["aml_case_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "get_business_clearance":
            return self.service.get_business_clearance(context, path["application_id"], correlation_id=correlation_id)
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
    ("POST", "/applications/{application_id}/aml-declarations", "submit_aml_declaration"),
    ("POST", "/aml/screenings", "start_aml_screening"),
    ("POST", "/webhooks/aml-screenings", "receive_aml_screening"),
    ("GET", "/aml/cases", "list_aml_cases"),
    ("GET", "/aml/cases/{aml_case_id}", "get_aml_case"),
    ("POST", "/aml/cases/{aml_case_id}/actions", "add_aml_case_action"),
    ("POST", "/aml/cases/{aml_case_id}/decision", "decide_aml_case"),
    ("GET", "/applications/{application_id}/business-clearance", "get_business_clearance"),
)


def create_fastapi_router(service: AmlCftService, context_resolver: Callable[[Any], ActorContext]) -> Any:
    """Build the integration router without forcing FastAPI into this module's lockfile.

    The build steward owns shared dependencies. Importing this module and running
    domain tests therefore remains possible before FastAPI is installed.
    """

    try:
        from fastapi import APIRouter, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover - exercised by integration composition
        raise RuntimeError("FastAPI must be supplied by the shared application build") from exc

    controller = M04Controller(service)
    router = APIRouter(prefix="/api/v1", tags=["M04"])

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
