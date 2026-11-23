from __future__ import annotations

from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from longhao_platform.bus import CommandBus, EventDispatcher, HandlerSpec
from longhao_platform.contracts import EventCatalog, ROOT
from longhao_platform.store import InMemoryAtomicStore
from longhao_platform.types import Command, HandlerResult, PendingEvent, Principal


TENANT_ID = UUID("3e624789-e990-5c6b-bf39-82de2afce2c8")


def test_verified_signature_automatically_creates_exact_publication_once():
    catalog = EventCatalog.load(ROOT / "events/registry.json")
    store = InMemoryAtomicStore(catalog)
    bus = CommandBus(store)
    dispatcher = EventDispatcher(store, catalog)
    analyst = Principal(uuid4(), TENANT_ID, "analyst@synthetic.invalid", frozenset({"analyst_signer"}))
    worker = Principal(uuid4(), TENANT_ID, "publication-worker@synthetic.invalid", frozenset({"platform_worker"}), "service")
    delivered = []

    report_id = uuid4()
    publication_id = None
    bundle_digest = "b" * 64
    report_digest = "c" * 64

    def verify_signature(_state, command, _principal):
        payload = {
            "signature_id": str(uuid4()),
            "report_version_id": str(command.aggregate_id),
            "report_content_sha256": report_digest,
            "bundle_manifest_sha256": bundle_digest,
            "signed_file_sha256": "d" * 64,
            "verified_at": "2026-11-23T00:00:00Z",
            "valid_until": "2026-12-23T00:00:00Z",
        }
        return HandlerResult(
            state={"signature_status": "verified", **payload},
            events=(PendingEvent("report.signature_verified", payload),),
            response={"signature_status": "verified"},
            classification="internal",
            changed_fields=("signature_status",),
        )

    def create_publication(_state, command, _principal):
        event_payload = {
            "publication_id": str(command.aggregate_id),
            "report_version_id": command.payload["report_version_id"],
            "signature_id": command.payload["signature_id"],
            "audience_snapshot_id": str(uuid4()),
            "bundle_manifest_sha256": command.payload["bundle_manifest_sha256"],
            "valid_until": command.payload["valid_until"],
        }
        return HandlerResult(
            state={"status": "published", **command.payload},
            events=(PendingEvent("publication.created", event_payload),),
            response={"status": "published"},
            classification="internal",
            changed_fields=("status",),
        )

    bus.register(
        HandlerSpec(
            "report.verify_signature",
            "report_version",
            "M07",
            frozenset({"analyst_signer"}),
            verify_signature,
        )
    )
    bus.register(
        HandlerSpec(
            "publication.create_from_verified_signature",
            "publication",
            "M08",
            frozenset({"platform_worker"}),
            create_publication,
        )
    )

    def on_signature(event):
        nonlocal publication_id
        publication_id = uuid5(NAMESPACE_URL, f"publication:{event.event_id}")
        bus.execute(
            Command(
                "publication.create_from_verified_signature",
                "publication",
                publication_id,
                0,
                f"signature:{event.event_id}",
                {
                    "report_version_id": event.payload["report_version_id"],
                    "signature_id": event.payload["signature_id"],
                    "report_content_sha256": event.payload["report_content_sha256"],
                    "bundle_manifest_sha256": event.payload["bundle_manifest_sha256"],
                    "valid_until": event.payload["valid_until"],
                },
                causation_id=event.event_id,
            ),
            worker,
        )

    dispatcher.register("report.signature_verified", "M08", on_signature)
    dispatcher.register("publication.created", "M09", lambda event: delivered.append(event.payload))

    result = bus.execute(
        Command(
            "report.verify_signature",
            "report_version",
            report_id,
            0,
            "signature:verify:0001",
            {"provider_result": "synthetic-verified"},
        ),
        analyst,
    )
    summary = dispatcher.drain()
    dispatcher.drain()

    assert result.version == 1
    assert summary["failed"] == 0
    assert len(delivered) == 1
    assert delivered[0]["report_version_id"] == str(report_id)
    assert delivered[0]["bundle_manifest_sha256"] == bundle_digest
    assert publication_id is not None
    publication = store.aggregate(TENANT_ID, "publication", publication_id)
    assert publication is not None
    assert publication.state["report_content_sha256"] == report_digest
    assert [row.event_type for row in store.outbox()] == ["report.signature_verified", "publication.created"]
    assert {row.status for row in store.outbox()} == {"published"}
    assert len(store.audits()) == 2


def test_missing_downstream_module_keeps_event_pending_instead_of_claiming_success():
    catalog = EventCatalog.load(ROOT / "events/registry.json")
    store = InMemoryAtomicStore(catalog)
    bus = CommandBus(store)
    dispatcher = EventDispatcher(store, catalog)
    analyst = Principal(uuid4(), TENANT_ID, "analyst@synthetic.invalid", frozenset({"analyst_signer"}))

    def verify(_state, command, _principal):
        payload = {
            "signature_id": str(uuid4()),
            "report_version_id": str(command.aggregate_id),
            "report_content_sha256": "a" * 64,
            "bundle_manifest_sha256": "b" * 64,
            "signed_file_sha256": "c" * 64,
            "verified_at": "2026-11-23T00:00:00Z",
            "valid_until": "2026-12-23T00:00:00Z",
        }
        return HandlerResult(
            state={"verified": True},
            events=(PendingEvent("report.signature_verified", payload),),
            classification="internal",
        )

    bus.register(HandlerSpec("report.verify_signature", "report_version", "M07", frozenset({"analyst_signer"}), verify))
    bus.execute(
        Command("report.verify_signature", "report_version", uuid4(), 0, "signature:missing:0001", {}),
        analyst,
    )
    summary = dispatcher.drain()

    assert summary["waiting_for_modules"] == ["M08"]
    assert summary["pending"] == 1
    assert store.outbox()[0].status == "waiting_for_modules"
