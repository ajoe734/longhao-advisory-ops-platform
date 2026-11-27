from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import inspect
from time import perf_counter
from typing import Any, Callable

from .domain import ActorContext, DomainError, ValidationError, new_id
from .service import FinanceControlService, ServiceResponse
from .telemetry import M14Telemetry


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


_IDEMPOTENT_REPLAY_OPS = {
    "create_invoice",
    "create_payment_intent",
    "receive_payment_webhook",
    "import_settlement",
    "resolve_reconciliation_item",
    "run_revenue_recognition",
    "approve_revenue_run",
    "request_refund",
    "approve_refund",
    "execute_refund",
    "receive_refund_webhook",
    "create_accounting_export",
    "acknowledge_accounting_export",
    "create_evidence_export",
    "create_legal_hold",
    "release_legal_hold",
    "record_control_exception",
    "act_on_control_exception",
}


class M14Controller:
    """Framework-neutral HTTP adapter for all published M14 operationIds."""

    def __init__(self, service: FinanceControlService, telemetry: M14Telemetry | None = None) -> None:
        self.service = service
        self.telemetry = telemetry or M14Telemetry()

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
            if operation_id in _IDEMPOTENT_REPLAY_OPS:
                response_headers["Idempotency-Replayed"] = str(response.idempotency_replayed).lower()
            result = ApiResponse(response.status, response.data, response_headers)
            self.telemetry.observe_request(operation_id, "success", perf_counter() - started)
            return result
        except DomainError as exc:
            field_errors = []
            if exc.field_name:
                field_errors.append({"field": exc.field_name, "code": exc.code, "message": exc.detail})
            problem = {
                "type": f"https://problems.example.invalid/m14/{exc.code}",
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
            result = ApiResponse(exc.status, problem, {"X-Correlation-Id": correlation_id, "Content-Type": "application/problem+json"})
            self.telemetry.observe_request(operation_id, exc.code, perf_counter() - started)
            if exc.status == 503:
                self.telemetry.dependency_failure(operation_id)
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
        service = self.service
        if operation_id == "create_invoice":
            return service.create_invoice(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "create_payment_intent":
            return service.create_payment_intent(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "receive_payment_webhook":
            return service.receive_payment_webhook(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "import_settlement":
            return service.import_settlement(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "list_reconciliation_items":
            return service.list_reconciliation_items(
                context,
                state_filter=query.get("state"),
                cursor=query.get("cursor"),
                limit=self._limit(query.get("limit")),
                as_of=self._as_of(query.get("as_of")),
                correlation_id=correlation_id,
            )
        if operation_id == "resolve_reconciliation_item":
            return service.resolve_reconciliation_item(context, path["item_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "run_revenue_recognition":
            return service.run_revenue_recognition(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "get_revenue_run":
            return service.get_revenue_run(context, path["run_id"], correlation_id=correlation_id)
        if operation_id == "approve_revenue_run":
            return service.approve_revenue_run(context, path["run_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "request_refund":
            return service.request_refund(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "get_refund":
            return service.get_refund(context, path["refund_id"], correlation_id=correlation_id)
        if operation_id == "approve_refund":
            return service.approve_refund(context, path["refund_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "execute_refund":
            return service.execute_refund(context, path["refund_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "receive_refund_webhook":
            return service.receive_refund_webhook(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "create_accounting_export":
            return service.create_accounting_export(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "get_accounting_export":
            return service.get_accounting_export(context, path["export_id"], correlation_id=correlation_id)
        if operation_id == "acknowledge_accounting_export":
            return service.acknowledge_accounting_export(context, path["export_id"], body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "list_audit_events":
            return service.list_audit_events(
                context,
                cursor=query.get("cursor"),
                limit=self._limit(query.get("limit")),
                as_of=self._as_of(query.get("as_of")),
                correlation_id=correlation_id,
            )
        if operation_id == "create_evidence_export":
            return service.create_evidence_export(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "get_evidence_export":
            return service.get_evidence_export(context, path["export_id"], correlation_id=correlation_id)
        if operation_id == "create_legal_hold":
            return service.create_legal_hold(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "release_legal_hold":
            return service.release_legal_hold(context, path["hold_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "record_control_exception":
            return service.record_control_exception(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "act_on_control_exception":
            return service.act_on_control_exception(context, path["exception_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
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
    ("POST", "/finance/invoices", "create_invoice"),
    ("POST", "/finance/payment-intents", "create_payment_intent"),
    ("POST", "/webhooks/payments", "receive_payment_webhook"),
    ("POST", "/finance/settlement-imports", "import_settlement"),
    ("GET", "/finance/reconciliation-items", "list_reconciliation_items"),
    ("POST", "/finance/reconciliation-items/{item_id}/resolution", "resolve_reconciliation_item"),
    ("POST", "/finance/revenue-runs", "run_revenue_recognition"),
    ("GET", "/finance/revenue-runs/{run_id}", "get_revenue_run"),
    ("POST", "/finance/revenue-runs/{run_id}/approval", "approve_revenue_run"),
    ("POST", "/finance/refunds", "request_refund"),
    ("GET", "/finance/refunds/{refund_id}", "get_refund"),
    ("POST", "/finance/refunds/{refund_id}/approval", "approve_refund"),
    ("POST", "/finance/refunds/{refund_id}/execution", "execute_refund"),
    ("POST", "/webhooks/refunds", "receive_refund_webhook"),
    ("POST", "/finance/accounting-exports", "create_accounting_export"),
    ("GET", "/finance/accounting-exports/{export_id}", "get_accounting_export"),
    ("POST", "/finance/accounting-exports/{export_id}/acknowledgement", "acknowledge_accounting_export"),
    ("GET", "/audit/events", "list_audit_events"),
    ("POST", "/audit/evidence-exports", "create_evidence_export"),
    ("GET", "/audit/evidence-exports/{export_id}", "get_evidence_export"),
    ("POST", "/audit/legal-holds", "create_legal_hold"),
    ("POST", "/audit/legal-holds/{hold_id}/release", "release_legal_hold"),
    ("POST", "/audit/control-exceptions", "record_control_exception"),
    ("POST", "/audit/control-exceptions/{exception_id}/actions", "act_on_control_exception"),
)


def create_fastapi_router(service: FinanceControlService, context_resolver: Callable[[Any], ActorContext]) -> Any:
    """Build the integration router without forcing FastAPI into this module's lockfile.

    The build steward owns shared dependencies. Importing this module and running
    domain tests therefore remains possible before FastAPI is installed.
    """

    try:
        from fastapi import APIRouter, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover - exercised by integration composition
        raise RuntimeError("FastAPI must be supplied by the shared application build") from exc

    controller = M14Controller(service)
    router = APIRouter(tags=["M14"])

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
