"""Module discovery and shared platform composition root."""
from __future__ import annotations

import importlib
import json
import sys
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol
from uuid import UUID

from longhao_platform.bus import CommandBus, EventDispatcher
from longhao_platform.contracts import EventCatalog, PlatformConfig, ROOT
from longhao_platform.store import InMemoryAtomicStore
from longhao_platform.types import PlatformError, Principal, Request


SYNTHETIC_TENANT_ID = UUID("3e624789-e990-5c6b-bf39-82de2afce2c8")
SYNTHETIC_ADMIN_ID = UUID("7dc75c5a-fcc2-5ace-b905-fd5d6405d840")
SYNTHETIC_WORKER_ID = UUID("3e8b6b56-35ef-5bf8-9cb7-ef680b2f76c2")


class Authenticator(Protocol):
    def authenticate(self, request: Request) -> Principal: ...


class SyntheticAuthenticator:
    """Exact local markers; claims are never decoded from arbitrary bearer text."""

    _principals = {
        "Bearer synthetic-platform-admin": Principal(
            SYNTHETIC_ADMIN_ID,
            SYNTHETIC_TENANT_ID,
            "platform-admin@synthetic.invalid",
            frozenset({"platform_admin", "internal_auditor"}),
        ),
        "Bearer synthetic-worker": Principal(
            SYNTHETIC_WORKER_ID,
            SYNTHETIC_TENANT_ID,
            "platform-worker@synthetic.invalid",
            frozenset({"platform_worker"}),
            actor_type="service",
        ),
    }

    def authenticate(self, request: Request) -> Principal:
        if request.headers.get("x-tenant-id"):
            raise PlatformError(400, "UNTRUSTED_TENANT_HEADER", "Tenant context comes from verified identity only.")
        principal = self._principals.get(request.headers.get("authorization", ""))
        if principal is None:
            raise PlatformError(401, "AUTHENTICATION_REQUIRED")
        return principal


@dataclass(frozen=True, slots=True)
class ModuleStatus:
    module_id: str
    slug: str
    title: str
    status: str
    error_code: str | None = None


class PlatformRuntime:
    """Composition surface used by M01-M14 ``plugin.register(runtime)`` hooks."""

    def __init__(self, config: PlatformConfig, catalog: EventCatalog, authenticator: Authenticator):
        self.config = config
        self.catalog = catalog
        self.authenticator = authenticator
        self.store = InMemoryAtomicStore(catalog)
        self.commands = CommandBus(self.store)
        self.events = EventDispatcher(self.store, catalog)
        self.module_statuses: list[ModuleStatus] = []
        self.routers: list[Any] = []

    @classmethod
    def bootstrap(
        cls,
        config: PlatformConfig | None = None,
        *,
        authenticator: Authenticator | None = None,
        discover_modules: bool = True,
    ) -> "PlatformRuntime":
        config = config or PlatformConfig.from_env()
        if authenticator is None:
            if config.environment == "production":
                raise PlatformError(500, "PRODUCTION_AUTHENTICATOR_REQUIRED")
            authenticator = SyntheticAuthenticator()
        runtime = cls(config, EventCatalog.load(config.event_registry_path), authenticator)
        if discover_modules:
            runtime.discover_modules()
        return runtime

    def include_router(self, router: Any) -> None:
        if router in self.routers:
            raise PlatformError(500, "DUPLICATE_ROUTER")
        self.routers.append(router)

    def discover_modules(self) -> None:
        try:
            manifest = json.loads(self.config.manifest_path.read_text(encoding="utf-8"))
            modules = manifest["modules"]
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PlatformError(500, "MODULE_MANIFEST_INVALID") from exc
        expected_ids = {f"M{number:02d}" for number in range(1, 15)}
        if {row.get("id") for row in modules} != expected_ids:
            raise PlatformError(500, "MODULE_MANIFEST_INCOMPLETE")

        source_root = ROOT / "apps/api/src"
        if str(source_root) not in sys.path:
            sys.path.insert(0, str(source_root))
        statuses: list[ModuleStatus] = []
        for row in modules:
            module_id, slug, title = row["id"], row["slug"], row["title"]
            import_name = f"modules.{slug}.plugin"
            try:
                plugin = importlib.import_module(import_name)
            except ModuleNotFoundError as exc:
                absent_names = {"modules", f"modules.{slug}", import_name}
                status = "missing" if exc.name in absent_names else "broken"
                code = "MODULE_NOT_INTEGRATED" if status == "missing" else "MODULE_DEPENDENCY_MISSING"
                statuses.append(ModuleStatus(module_id, slug, title, status, code))
                continue
            except Exception:
                statuses.append(ModuleStatus(module_id, slug, title, "broken", "MODULE_IMPORT_FAILED"))
                continue
            try:
                if getattr(plugin, "MODULE_ID", module_id) != module_id:
                    raise PlatformError(500, "MODULE_ID_MISMATCH")
                register = getattr(plugin, "register", None)
                if not callable(register):
                    raise PlatformError(500, "MODULE_REGISTER_MISSING")
                register(self)
                statuses.append(ModuleStatus(module_id, slug, title, "loaded"))
            except PlatformError as exc:
                statuses.append(ModuleStatus(module_id, slug, title, "broken", exc.code))
            except Exception:
                statuses.append(ModuleStatus(module_id, slug, title, "broken", "MODULE_REGISTRATION_FAILED"))
        self.module_statuses = statuses

    def readiness(self, *, disclose_modules: bool = True) -> tuple[int, Mapping[str, object]]:
        loaded = sum(row.status == "loaded" for row in self.module_statuses)
        missing = [row.module_id for row in self.module_statuses if row.status == "missing"]
        broken = [row.module_id for row in self.module_statuses if row.status == "broken"]
        complete = loaded == 14 and not broken
        ready = complete or (self.config.allow_missing_modules and not broken)
        body: dict[str, object] = {
            "status": "ready" if complete else "degraded" if ready else "not_ready",
            "environment": self.config.environment,
            "provider_mode": "synthetic" if self.config.synthetic_providers else "configured",
            "module_count": 14,
            "loaded_modules": loaded,
            "missing_module_count": len(missing),
            "broken_module_count": len(broken),
            "event_contracts": len(self.catalog),
            "release_gate_passed": complete,
        }
        if disclose_modules:
            body.update(
                missing_modules=missing,
                broken_modules=broken,
                modules=[asdict(row) for row in self.module_statuses],
            )
        return (200 if ready else 503), body

    @staticmethod
    def require_roles(principal: Principal, allowed: frozenset[str]) -> None:
        if not principal.enabled or principal.roles.isdisjoint(allowed):
            raise PlatformError(403, "AUTHORIZATION_DENIED")
