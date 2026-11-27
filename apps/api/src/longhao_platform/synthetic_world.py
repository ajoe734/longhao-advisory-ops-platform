"""Local/development composition that lets the fourteen modules work together.

Each module keeps its own repository and its own fake ports, so the module
contracts and their production guards are unchanged. This world only adds what
a single process needs for the modules to see each other:

* one synthetic object-asset registry: registering an asset makes it known to
  every module's fake evidence/object store;
* an in-process event relay: pending outbox events of every module are
  delivered, as their published envelopes, to the consumers listed in
  ``events/registry.json``;
* projections: small dev-only handlers that keep downstream fake lookup ports
  (e.g. M02's application source) in step with upstream module events.

Nothing here is used in production: plugins only bind to the world when the
platform runs with synthetic providers, and every module service refuses its
fake ports in production anyway.
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID, uuid5

from longhao_platform.contracts import ROOT
from longhao_platform.synthetic_identities import SYNTHETIC_TENANT_ID

_WORLDS_LOCK = threading.Lock()
_MAX_RETRIES = 10
_ASSET_NAMESPACE = UUID("0b8c7c55-34f1-5d43-9d3a-6f2a1e5b7c10")

EvidenceSink = Callable[[str, str, "AssetRecord"], None]
Projection = Callable[["SyntheticWorld", dict[str, Any]], None]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class AssetRecord:
    asset_id: str
    label: str
    sha256: str
    classification: str = "internal"
    customer_id: str | None = None
    content: str = ""

    def public_view(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "label": self.label,
            "sha256": self.sha256,
            "classification": self.classification,
            "customer_id": self.customer_id,
        }


@dataclass(slots=True)
class ModuleBinding:
    module_id: str
    repository: Any
    consumer: Any | None = None
    services: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeliveryRecord:
    event_id: str
    event_type: str
    producer: str
    target: str
    status: str
    code: str
    at: str


class SyntheticWorld:
    """One per PlatformRuntime; only bound when synthetic providers are enabled."""

    def __init__(self, tenant_id: UUID = SYNTHETIC_TENANT_ID) -> None:
        self.tenant_id = str(tenant_id)
        self.modules: dict[str, ModuleBinding] = {}
        self.assets: dict[str, AssetRecord] = {}
        self._sinks: list[EvidenceSink] = []
        self._projections: dict[str, list[Projection]] = {}
        self._early_projections: dict[str, list[Projection]] = {}
        self._retries: dict[tuple[str, str], tuple[dict[str, Any], str, int]] = {}
        self._relayed: set[str] = set()
        self.deliveries: deque[DeliveryRecord] = deque(maxlen=500)
        self.failures: deque[dict[str, Any]] = deque(maxlen=200)
        self.events: deque[dict[str, Any]] = deque(maxlen=1000)
        self.workers: list[tuple[str, Callable[[], Any]]] = []
        self.customer_links: dict[str, str] = {}
        self._lock = threading.RLock()
        self._registry = self._load_registry()

    # ------------------------------------------------------------------ binding
    @staticmethod
    def _load_registry() -> dict[str, dict[str, Any]]:
        rows = json.loads((ROOT / "events/registry.json").read_text(encoding="utf-8"))
        return {row["event_type"]: row for row in rows}

    def bind(
        self,
        module_id: str,
        repository: Any,
        *,
        consumer: Any | None = None,
        evidence_sink: EvidenceSink | None = None,
        **services: Any,
    ) -> ModuleBinding:
        with self._lock:
            binding = ModuleBinding(module_id, repository, consumer, dict(services))
            self.modules[module_id] = binding
            if evidence_sink is not None:
                self._sinks.append(evidence_sink)
                for record in self.assets.values():
                    evidence_sink(self.tenant_id, record.asset_id, record)
            return binding

    def on(self, event_type: str, projection: Projection, *, before_consumers: bool = False) -> None:
        if event_type not in self._registry:
            raise ValueError(f"unknown event type {event_type}")
        with self._lock:
            target = self._early_projections if before_consumers else self._projections
            target.setdefault(event_type, []).append(projection)

    def service(self, module_id: str, name: str) -> Any:
        binding = self.modules.get(module_id)
        return None if binding is None else binding.services.get(name)

    def add_worker(self, name: str, run_once: Callable[[], Any]) -> None:
        """A module background job (scan, dispatch, sweep...) run on every tick."""
        with self._lock:
            self.workers.append((name, run_once))

    # --------------------------------------------------------- customer links
    def link_customer(self, actor_id: str, customer_id: str) -> None:
        """A signed-in member (LegendFlow identity) acts as this M01 customer."""
        with self._lock:
            self.customer_links[str(actor_id)] = str(customer_id)

    def customer_for(self, actor_id: str) -> str:
        return self.customer_links.get(str(actor_id), str(actor_id))

    # ------------------------------------------------------------------- assets
    def register_asset(
        self,
        label: str,
        *,
        content: str | None = None,
        classification: str = "internal",
        customer_id: str | None = None,
        asset_id: str | None = None,
    ) -> AssetRecord:
        body = content if content is not None else f"synthetic asset: {label}"
        record = AssetRecord(
            asset_id=asset_id or str(uuid5(_ASSET_NAMESPACE, f"{self.tenant_id}:{label}")),
            label=label,
            sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            classification=classification,
            customer_id=customer_id,
            content=body,
        )
        with self._lock:
            self.assets[record.asset_id] = record
            for sink in self._sinks:
                sink(self.tenant_id, record.asset_id, record)
        return record

    # -------------------------------------------------------------------- relay
    def _pending(self) -> list[tuple[str, Any]]:
        rows: list[tuple[str, Any]] = []
        for module_id, binding in self.modules.items():
            try:
                events = binding.repository.read(lambda state: list(getattr(state, "outbox", {}).values()))
            except Exception:  # repository without an outbox
                continue
            for event in events:
                if event.event_id not in self._relayed:
                    rows.append((module_id, event))
        rows.sort(key=lambda row: (row[1].emitted_at, row[1].sequence_no))
        return rows

    def _project(self, envelope: dict[str, Any], projections: list[Projection]) -> None:
        for projection in projections:
            try:
                projection(self, envelope)
            except Exception as exc:
                self.failures.append({
                    "event_id": envelope["event_id"],
                    "event_type": envelope["event_type"],
                    "target": f"projection:{getattr(projection, '__qualname__', projection)}",
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    "trace": traceback.format_exc(limit=4)[-1200:],
                })

    def _deliver(self, envelope: dict[str, Any], producer: str, target: str) -> bool:
        binding = self.modules.get(target)
        if binding is None or binding.consumer is None:
            self._record(envelope, producer, target, "skipped", "CONSUMER_NOT_BOUND")
            return False
        key = (envelope["event_id"], target)
        try:
            result = binding.consumer.consume(envelope)
        except Exception as exc:  # recorded for the operator; never crashes the request
            self._retries.pop(key, None)
            self._record(envelope, producer, target, "failed", type(exc).__name__)
            self.failures.append({
                "event_id": envelope["event_id"],
                "event_type": envelope["event_type"],
                "target": target,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                "trace": traceback.format_exc(limit=4)[-1200:],
            })
            return False
        status = getattr(result, "status", None) or (result.get("status") if isinstance(result, dict) else "ok")
        code = getattr(result, "result_code", None) or (result.get("result_code") if isinstance(result, dict) else "")
        self._record(envelope, producer, target, status, code or "")
        if status == "retry":
            attempts = self._retries.get(key, (envelope, producer, 0))[2] + 1
            if attempts < _MAX_RETRIES:
                self._retries[key] = (envelope, producer, attempts)
            else:
                self._retries.pop(key, None)
                self.failures.append({"event_id": envelope["event_id"], "event_type": envelope["event_type"],
                                      "target": target, "error": f"still retry after {attempts} attempts: {code}", "trace": ""})
        else:
            self._retries.pop(key, None)
        return True

    def relay(self, *, max_rounds: int = 25) -> int:
        """Deliver every pending outbox event; repeat while consumers emit more.

        Projections registered with ``before_consumers`` run first (a downstream
        lookup the consumer itself needs); the others run after the consumers
        (they read the state the consumers just applied). Deliveries a consumer
        answered with ``retry`` are attempted again on later rounds.
        """
        delivered = 0
        with self._lock:
            for _round in range(max_rounds):
                pending = self._pending()
                retries = list(self._retries.items())
                if not pending and not retries:
                    break
                for (_event_id, target), (envelope, producer, _attempts) in retries:
                    delivered += self._deliver(envelope, producer, target)
                for producer, event in pending:
                    self._relayed.add(event.event_id)
                    envelope = event.envelope()
                    self.events.append(envelope)
                    self._project(envelope, self._early_projections.get(envelope["event_type"], []))
                    for target in self._registry.get(envelope["event_type"], {}).get("consumers", []):
                        delivered += self._deliver(envelope, producer, target)
                    self._project(envelope, self._projections.get(envelope["event_type"], []))
                if not pending and len(self._retries) == len(retries):
                    break  # only unchanged retries left; try again on the next tick
        return delivered

    def tick(self, *, rounds: int = 4) -> int:
        """Run every module worker once, then relay; repeat while events keep flowing."""
        total = 0
        with self._lock:
            for _round in range(rounds):
                for name, run_once in self.workers:
                    try:
                        run_once()
                    except Exception as exc:  # a failing job is recorded, never fatal to the request
                        self.failures.append({
                            "event_id": None,
                            "event_type": None,
                            "target": f"worker:{name}",
                            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                            "trace": traceback.format_exc(limit=4)[-1200:],
                        })
                before = len(self._relayed)
                total += self.relay()
                if len(self._relayed) == before:
                    break
        return total

    def _record(self, envelope: dict[str, Any], producer: str, target: str, status: str, code: str) -> None:
        self.deliveries.append(DeliveryRecord(
            envelope["event_id"], envelope["event_type"], producer, target, status, code, utc_now().isoformat(),
        ))

    # ------------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "tenant_id": self.tenant_id,
                "bound_modules": sorted(self.modules),
                "consumers": sorted(m for m, b in self.modules.items() if b.consumer is not None),
                "workers": [name for name, _run in self.workers],
                "assets": [record.public_view() for record in self.assets.values()],
                "relayed_events": len(self._relayed),
                "pending_retries": [{"event_id": k[0], "target": k[1], "attempts": v[2]} for k, v in self._retries.items()],
                "recent_deliveries": [vars_of(d) for d in list(self.deliveries)[-50:]],
                "failures": list(self.failures)[-50:],
            }


def vars_of(record: DeliveryRecord) -> dict[str, Any]:
    return {name: getattr(record, name) for name in record.__slots__}


_ATTRIBUTE = "_synthetic_world"


def world_for(runtime: Any) -> SyntheticWorld | None:
    """The runtime's world, or None when synthetic providers are disabled."""
    config = getattr(runtime, "config", None)
    if config is None or getattr(config, "environment", "production") == "production":
        return None
    if not getattr(config, "synthetic_providers", False):
        return None
    with _WORLDS_LOCK:
        world = getattr(runtime, _ATTRIBUTE, None)
        if world is None:
            world = SyntheticWorld()
            setattr(runtime, _ATTRIBUTE, world)
            if str(ROOT) not in sys.path:
                sys.path.append(str(ROOT))  # workers.tasks.* consumers live at the repository root
            from longhao_platform import synthetic_projections

            synthetic_projections.install(world)
        return world


def existing_world(runtime: Any) -> SyntheticWorld | None:
    return getattr(runtime, _ATTRIBUTE, None)


def latest_event(world: SyntheticWorld, event_type: str, **payload_match: Any) -> dict[str, Any] | None:
    """Most recent relayed envelope of ``event_type`` whose payload matches the given fields."""
    for envelope in reversed(world.events):
        if envelope["event_type"] == event_type and all(envelope["payload"].get(k) == v for k, v in payload_match.items()):
            return envelope
    return None
