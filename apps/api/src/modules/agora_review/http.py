from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import inspect
import json
from time import perf_counter
from typing import Any, Callable

from .domain import ActorContext, DomainError, ValidationError, new_id
from .ports import WebhookRequest
from .service import AgoraReviewService, ServiceResponse
from .telemetry import M06Telemetry


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


_COMMANDS_WITH_REPLAY_HEADER = {
    "assign_review_case",
    "open_servant_conversation",
    "request_servant_revision",
    "receive_servant_revision",
}


class M06Controller:
    """Framework-neutral HTTP adapter for all published M06 operationIds."""

    def __init__(self, service: AgoraReviewService, telemetry: M06Telemetry | None = None) -> None:
        self.service = service
        self.telemetry = telemetry or M06Telemetry()

    def handle(
        self,
        operation_id: str,
        context: ActorContext,
        *,
        body: dict[str, Any] | None = None,
        path: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        raw_body: bytes | None = None,
    ) -> ApiResponse:
        body = body or {}
        path = path or {}
        query = query or {}
        headers = {key.lower(): value for key, value in (headers or {}).items()}
        correlation_id = headers.get("x-correlation-id") or new_id()
        started = perf_counter()
        try:
            response = self._dispatch(operation_id, context, body, path, query, headers, correlation_id, raw_body)
            response_headers = {"X-Correlation-Id": correlation_id}
            if response.etag:
                response_headers["ETag"] = response.etag
            if operation_id in _COMMANDS_WITH_REPLAY_HEADER:
                response_headers["Idempotency-Replayed"] = str(response.idempotency_replayed).lower()
            result = ApiResponse(response.status, response.data, response_headers)
            self.telemetry.observe_request(operation_id, "success", perf_counter() - started)
            return result
        except DomainError as exc:
            field_errors = []
            if exc.field_name:
                field_errors.append({"field": exc.field_name, "code": exc.code, "message": exc.detail})
            problem = {
                "type": f"https://problems.example.invalid/m06/{exc.code}",
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
                self.telemetry.dependency_failure("servant_adapter")
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
        raw_body: bytes | None,
    ) -> ServiceResponse:
        key = headers.get("idempotency-key", "")
        version = self._if_match(headers.get("if-match"))
        if operation_id == "list_review_cases":
            return self.service.list_review_cases(
                context,
                state_filter=query.get("state"),
                cursor=query.get("cursor"),
                limit=self._limit(query.get("limit")),
                as_of=self._as_of(query.get("as_of")),
                correlation_id=correlation_id,
            )
        if operation_id == "get_review_case":
            return self.service.get_review_case(context, path["review_case_id"], correlation_id=correlation_id)
        if operation_id == "assign_review_case":
            return self.service.assign_review_case(
                context, path["review_case_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id
            )
        if operation_id == "open_servant_conversation":
            return self.service.open_servant_conversation(
                context, path["review_case_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id
            )
        if operation_id == "request_servant_revision":
            return self.service.request_servant_revision(
                context, path["review_case_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id
            )
        if operation_id == "receive_servant_revision":
            webhook = self._webhook_request(headers, raw_body)
            return self.service.receive_servant_revision(
                context, body, idempotency_key=key, correlation_id=correlation_id, webhook=webhook
            )
        if operation_id == "get_revision_request":
            return self.service.get_revision_request(context, path["revision_id"], correlation_id=correlation_id)
        raise ValidationError("unknown operation_id", code="operation_not_found")

    @staticmethod
    def _webhook_request(headers: dict[str, str], raw_body: bytes | None) -> WebhookRequest:
        signature = headers.get("x-webhook-signature")
        timestamp = headers.get("x-webhook-timestamp")
        nonce = headers.get("x-webhook-nonce")
        key_id = headers.get("x-integration-key-id")
        if not signature:
            raise ValidationError("X-Webhook-Signature is required", field_name="X-Webhook-Signature")
        if not timestamp:
            raise ValidationError("X-Webhook-Timestamp is required", field_name="X-Webhook-Timestamp")
        if not nonce:
            raise ValidationError("X-Webhook-Nonce is required", field_name="X-Webhook-Nonce")
        if not key_id:
            raise ValidationError("X-Integration-Key-Id is required", field_name="X-Integration-Key-Id")
        if raw_body is None:
            raise ValidationError("raw request body is required to verify the webhook signature", field_name="raw_body")
        webhook_path = next(path for _, path, operation in ROUTES if operation == "receive_servant_revision")
        return WebhookRequest(
            signature=signature, timestamp=timestamp, nonce=nonce, integration_key_id=key_id,
            raw_body=raw_body, method="POST", path=f"/api/v1{webhook_path}",
        )

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
    ("GET", "/review-cases", "list_review_cases"),
    ("GET", "/review-cases/{review_case_id}", "get_review_case"),
    ("POST", "/review-cases/{review_case_id}/assignment", "assign_review_case"),
    ("POST", "/review-cases/{review_case_id}/conversations", "open_servant_conversation"),
    ("POST", "/review-cases/{review_case_id}/revision-requests", "request_servant_revision"),
    ("POST", "/webhooks/servant-revisions", "receive_servant_revision"),
    ("GET", "/revision-requests/{revision_id}", "get_revision_request"),
)


def create_fastapi_router(service: AgoraReviewService, context_resolver: Callable[[Any], ActorContext]) -> Any:
    """Build the integration router without forcing FastAPI into this module's lockfile.

    The build steward owns shared dependencies. Importing this module and running
    domain tests therefore remains possible before FastAPI is installed.
    """

    try:
        from fastapi import APIRouter, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover - exercised by integration composition
        raise RuntimeError("FastAPI must be supplied by the shared application build") from exc

    controller = M06Controller(service)
    router = APIRouter(prefix="/api/v1", tags=["M06"])

    def endpoint_for(operation_id: str, method: str) -> Callable[..., Any]:
        async def endpoint(request: Request) -> JSONResponse:
            context = context_resolver(request)
            if inspect.isawaitable(context):
                context = await context
            request_body: dict[str, Any] = {}
            raw_body: bytes | None = None
            if method not in {"GET", "HEAD"}:
                raw_body = await request.body()
                try:
                    request_body = json.loads(raw_body) if raw_body else {}
                except Exception:  # FastAPI/Starlette raises different decode errors by version.
                    request_body = {}
            result = controller.handle(
                operation_id,
                context,
                body=request_body,
                path=dict(request.path_params),
                query=dict(request.query_params),
                headers=dict(request.headers),
                raw_body=raw_body,
            )
            return JSONResponse(result.body, status_code=result.status, headers=result.headers)

        endpoint.__name__ = operation_id
        endpoint.__annotations__ = {"request": Request, "return": JSONResponse}
        return endpoint

    for method, path, operation_id in ROUTES:
        router.add_api_route(path, endpoint_for(operation_id, method), methods=[method], operation_id=operation_id)
    return router
