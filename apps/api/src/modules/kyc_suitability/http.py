from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from time import perf_counter
from typing import Any, Callable

from .domain import ActorContext, DomainError, ValidationError, new_id
from .service import KycSuitabilityService, ServiceResponse
from .telemetry import M03Telemetry


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


_IDEMPOTENT_OPERATIONS = {
    "create_kyc_submission",
    "request_kyc_upload",
    "submit_kyc",
    "decide_kyc",
    "assess_suitability",
    "request_due_diligence_refresh",
}


class M03Controller:
    """Framework-neutral HTTP adapter for all published M03 operationIds."""

    def __init__(self, service: KycSuitabilityService, telemetry: M03Telemetry | None = None) -> None:
        self.service = service
        self.telemetry = telemetry or M03Telemetry()

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
                "type": f"https://problems.example.invalid/m03/{exc.code}",
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
        if operation_id == "create_kyc_submission":
            return self.service.create_kyc_submission(
                context, path["application_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id
            )
        if operation_id == "request_kyc_upload":
            return self.service.request_kyc_upload(
                context, path["submission_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id
            )
        if operation_id == "submit_kyc":
            return self.service.submit_kyc(
                context, path["submission_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id
            )
        if operation_id == "get_kyc_submission":
            return self.service.get_kyc_submission(context, path["submission_id"], correlation_id=correlation_id)
        if operation_id == "decide_kyc":
            return self.service.decide_kyc(
                context, path["submission_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id
            )
        if operation_id == "assess_suitability":
            return self.service.assess_suitability(
                context, path["application_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id
            )
        if operation_id == "list_suitability_assessments":
            return self.service.list_suitability_assessments(context, path["application_id"], correlation_id=correlation_id)
        if operation_id == "get_suitability_assessment":
            return self.service.get_suitability_assessment(context, path["assessment_id"], correlation_id=correlation_id)
        if operation_id == "request_due_diligence_refresh":
            return self.service.request_due_diligence_refresh(
                context, path["customer_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id
            )
        if operation_id == "get_document_status":
            return self.service.get_document_status(context, path["asset_id"], correlation_id=correlation_id)
        raise ValidationError("unknown operation_id", code="operation_not_found")

    @staticmethod
    def _if_match(value: str | None) -> int | None:
        if value is None:
            return None
        if len(value) < 3 or not value.startswith('"') or not value.endswith('"') or not value[1:-1].isdigit() or int(value[1:-1]) < 1:
            raise ValidationError("If-Match must be a strong integer ETag", field_name="If-Match")
        return int(value[1:-1])


ROUTES: tuple[tuple[str, str, str], ...] = (
    ("POST", "/applications/{application_id}/kyc-submissions", "create_kyc_submission"),
    ("POST", "/kyc-submissions/{submission_id}/document-uploads", "request_kyc_upload"),
    ("POST", "/kyc-submissions/{submission_id}/submit", "submit_kyc"),
    ("GET", "/kyc-submissions/{submission_id}", "get_kyc_submission"),
    ("POST", "/kyc-submissions/{submission_id}/decision", "decide_kyc"),
    ("POST", "/applications/{application_id}/suitability-assessments", "assess_suitability"),
    ("GET", "/applications/{application_id}/suitability-assessments", "list_suitability_assessments"),
    ("GET", "/suitability-assessments/{assessment_id}", "get_suitability_assessment"),
    ("POST", "/customers/{customer_id}/due-diligence-refreshes", "request_due_diligence_refresh"),
    ("GET", "/documents/{asset_id}/status", "get_document_status"),
)


def create_fastapi_router(
    service: KycSuitabilityService,
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

    controller = M03Controller(service)
    router = APIRouter(prefix="/api/v1", tags=["M03"])

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
