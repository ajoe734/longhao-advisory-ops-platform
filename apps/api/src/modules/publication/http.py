from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import inspect
from time import perf_counter
from typing import Any, Callable

from .domain import ActorContext, DomainError, ValidationError, new_id, parse_time
from .service import PublicationService, ServiceResponse
from .telemetry import M08Telemetry


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


_IDEMPOTENT_OPERATIONS = {
    "create_recommendation_series",
    "create_recommendation_version",
    "withdraw_publication",
    "link_publication_correction",
}


class M08Controller:
    """Framework-neutral HTTP adapter for all published M08 operationIds."""

    def __init__(self, service: PublicationService, telemetry: M08Telemetry | None = None) -> None:
        self.service = service
        self.telemetry = telemetry or M08Telemetry()

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
            return result
        except DomainError as exc:
            field_errors = []
            if exc.field_name:
                field_errors.append({"field": exc.field_name, "code": exc.code, "message": exc.detail})
            problem = {
                "type": f"https://problems.example.invalid/m08/{exc.code}",
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
                self.telemetry.dependency_failure(exc.code)
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
        if operation_id == "create_recommendation_series":
            return self.service.create_recommendation_series(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "create_recommendation_version":
            return self.service.create_recommendation_version(
                context, path["series_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id,
            )
        if operation_id == "list_publications":
            return self.service.list_publications(
                context,
                state_filter=query.get("state"),
                cursor=query.get("cursor"),
                limit=self._limit(query.get("limit")),
                as_of=self._as_of(query.get("as_of")),
                correlation_id=correlation_id,
            )
        if operation_id == "get_publication":
            return self.service.get_publication(context, path["publication_id"], correlation_id=correlation_id)
        if operation_id == "withdraw_publication":
            return self.service.withdraw_publication(
                context, path["publication_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id,
            )
        if operation_id == "link_publication_correction":
            return self.service.link_publication_correction(
                context, path["publication_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id,
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
        return parse_time(value, "as_of") if value is not None else None


ROUTES: tuple[tuple[str, str, str], ...] = (
    ("POST", "/recommendation-series", "create_recommendation_series"),
    ("POST", "/recommendation-series/{series_id}/versions", "create_recommendation_version"),
    ("GET", "/publications", "list_publications"),
    ("GET", "/publications/{publication_id}", "get_publication"),
    ("POST", "/publications/{publication_id}/withdrawal", "withdraw_publication"),
    ("POST", "/publications/{publication_id}/correction-links", "link_publication_correction"),
)


def create_fastapi_router(
    service: PublicationService,
    context_resolver: Callable[[Any], ActorContext],
) -> Any:
    """Builds the integration router without forcing FastAPI into this module's lockfile.

    The build steward owns the shared FastAPI dependency; importing this
    module and running domain tests remains possible without it installed.
    """

    try:
        from fastapi import APIRouter, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover - exercised by integration composition
        raise RuntimeError("FastAPI must be supplied by the shared application build") from exc

    controller = M08Controller(service)
    router = APIRouter(prefix="/api/v1", tags=["M08"])

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
