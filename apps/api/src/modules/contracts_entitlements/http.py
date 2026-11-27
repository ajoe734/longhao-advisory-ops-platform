from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import inspect
from time import perf_counter
from typing import Any, Callable

from .domain import ActorContext, DomainError, UnauthorizedError, ValidationError, new_id
from .ports import WebhookVerifierPort
from .service import ContractsEntitlementsService, ServiceResponse
from .telemetry import M02Telemetry


_IDEMPOTENT_OPERATIONS = {
    "create_contract_package", "approve_contract_package", "create_customer_signature", "receive_customer_signature",
    "decide_company_acceptance", "create_contract_amendment", "terminate_contract", "evaluate_entitlements",
    "suspend_entitlement",
}

@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


class M02Controller:
    """Framework-neutral HTTP adapter for all published M02 operationIds."""

    def __init__(self, service: ContractsEntitlementsService, webhook_verifier: WebhookVerifierPort, telemetry: M02Telemetry | None = None) -> None:
        self.service = service
        self.webhook_verifier = webhook_verifier
        self.telemetry = telemetry or M02Telemetry()

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
    ) -> ApiResponse:
        body = body or {}
        path = path or {}
        query = query or {}
        headers = {key.lower(): value for key, value in (headers or {}).items()}
        correlation_id = headers.get("x-correlation-id") or new_id()
        started = perf_counter()
        try:
            if operation_id == "receive_customer_signature":
                context = self._authenticate_webhook(headers, raw_body)
            if context is None:
                raise UnauthorizedError("no authenticated principal was supplied")
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
                "type": f"https://problems.example.invalid/m02/{exc.code}",
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
                self.telemetry.dependency_failure("gate_or_payment_port")
            return result

    def _authenticate_webhook(self, headers: dict[str, str], raw_body: bytes) -> ActorContext:
        timestamp = headers.get("x-webhook-timestamp", "")
        nonce = headers.get("x-webhook-nonce", "")
        key_id = headers.get("x-integration-key-id", "")
        signature = headers.get("x-webhook-signature", "")
        if not timestamp or not nonce or not key_id or not signature:
            raise ValidationError("webhook signature headers are incomplete", field_name="X-Webhook-Signature")
        tenant_id = self.webhook_verifier.resolve_tenant(key_id)
        verified = self.webhook_verifier.verify(
            timestamp=timestamp, nonce=nonce, method="POST", path="/api/v1/webhooks/customer-signatures",
            raw_body=raw_body, signature=signature, key_id=key_id,
        )
        if not verified:
            raise UnauthorizedError("webhook signature could not be verified", code="webhook_signature_invalid")
        return ActorContext(
            tenant_id, "00000000-0000-0000-0000-000000000002",
            frozenset({"signature_adapter"}), principal_type="service", purpose="webhook_integration",
        )

    def _dispatch(
        self, operation_id: str, context: ActorContext, body: dict[str, Any], path: dict[str, str],
        query: dict[str, Any], headers: dict[str, str], correlation_id: str,
    ) -> ServiceResponse:
        key = headers.get("idempotency-key", "")
        version = self._if_match(headers.get("if-match"))
        if operation_id == "create_contract_package":
            return self.service.create_contract_package(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "approve_contract_package":
            return self.service.approve_contract_package(context, path["package_version_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "get_contract_package":
            return self.service.get_contract_package(context, path["package_version_id"], correlation_id=correlation_id)
        if operation_id == "create_customer_signature":
            return self.service.create_customer_signature(context, path["application_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "receive_customer_signature":
            return self.service.receive_customer_signature(context, body, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "decide_company_acceptance":
            return self.service.decide_company_acceptance(context, path["application_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "get_contract":
            return self.service.get_contract(context, path["contract_id"], correlation_id=correlation_id)
        if operation_id == "create_contract_amendment":
            return self.service.create_contract_amendment(context, path["contract_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "terminate_contract":
            return self.service.terminate_contract(context, path["contract_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "list_customer_entitlements":
            return self.service.list_customer_entitlements(
                context, path["customer_id"], state_filter=query.get("state"), cursor=query.get("cursor"),
                limit=self._limit(query.get("limit")), as_of=self._as_of(query.get("as_of")), correlation_id=correlation_id,
            )
        if operation_id == "evaluate_entitlements":
            return self.service.evaluate_entitlements(context, path["contract_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
        if operation_id == "suspend_entitlement":
            return self.service.suspend_entitlement(context, path["entitlement_id"], body, expected_version=version, idempotency_key=key, correlation_id=correlation_id)
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
    ("POST", "/contract-packages", "create_contract_package"),
    ("POST", "/contract-packages/{package_version_id}/approval", "approve_contract_package"),
    ("GET", "/contract-packages/{package_version_id}", "get_contract_package"),
    ("POST", "/applications/{application_id}/customer-signature", "create_customer_signature"),
    ("POST", "/webhooks/customer-signatures", "receive_customer_signature"),
    ("POST", "/applications/{application_id}/company-acceptance", "decide_company_acceptance"),
    ("GET", "/contracts/{contract_id}", "get_contract"),
    ("POST", "/contracts/{contract_id}/amendments", "create_contract_amendment"),
    ("POST", "/contracts/{contract_id}/termination", "terminate_contract"),
    ("GET", "/customers/{customer_id}/entitlements", "list_customer_entitlements"),
    ("POST", "/contracts/{contract_id}/entitlement-evaluations", "evaluate_entitlements"),
    ("POST", "/entitlements/{entitlement_id}/suspension", "suspend_entitlement"),
)


def create_fastapi_router(service: ContractsEntitlementsService, webhook_verifier: WebhookVerifierPort, context_resolver: Callable[[Any], Any]) -> Any:
    """Build the integration router without forcing FastAPI into this module's lockfile.

    The build steward owns shared dependencies. Importing this module and running
    domain tests therefore remains possible before FastAPI is installed.
    """

    try:
        from fastapi import APIRouter, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover - exercised by integration composition
        raise RuntimeError("FastAPI must be supplied by the shared application build") from exc

    controller = M02Controller(service, webhook_verifier)
    router = APIRouter(tags=["M02"])

    def endpoint_for(operation_id: str, method: str) -> Callable[..., Any]:
        async def endpoint(request: Request) -> JSONResponse:
            raw_body = await request.body()
            context = None
            if operation_id != "receive_customer_signature":
                context = context_resolver(request)
                if inspect.isawaitable(context):
                    context = await context
            request_body: dict[str, Any] = {}
            if method not in {"GET", "HEAD"} and raw_body:
                try:
                    request_body = await request.json()
                except Exception:  # FastAPI/Starlette raises different decode errors by version.
                    request_body = {}
            result = controller.handle(
                operation_id, context, body=request_body, path=dict(request.path_params),
                query=dict(request.query_params), headers=dict(request.headers), raw_body=raw_body,
            )
            return JSONResponse(result.body, status_code=result.status, headers=result.headers)

        endpoint.__name__ = operation_id
        endpoint.__annotations__ = {"request": Request, "return": JSONResponse}
        return endpoint

    for method, path, operation_id in ROUTES:
        router.add_api_route(path, endpoint_for(operation_id, method), methods=[method], operation_id=operation_id)
    return router
