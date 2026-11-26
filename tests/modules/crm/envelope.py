"""Shared event-envelope test helper.

Kept out of ``conftest.py`` and under a basename unique across
``tests/modules/*`` on purpose: pytest resolves a bare ``import conftest``
(or ``from conftest import ...``) against whichever sibling test package's
``conftest`` module happens to be cached under that exact name in
``sys.modules`` last, which is not necessarily this directory's own
``conftest.py`` once more than one module under ``tests/modules`` is
collected in the same session.
"""
from __future__ import annotations

from uuid import uuid4

from modules.crm.domain import canonical_hash


def _new_id() -> str:
    return str(uuid4())


def make_envelope(
    *,
    event_type: str,
    schema_version: str = "1.0.0",
    tenant_id: str,
    producer: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int = 1,
    sequence_no: int = 1,
    occurred_at: str = "2026-11-20T00:00:00.000000Z",
    emitted_at: str = "2026-11-20T00:00:00.000000Z",
    correlation_id: str | None = None,
    causation_id: str | None = None,
    idempotency_key: str | None = None,
    classification: str = "customer_confidential",
    payload: dict,
) -> dict:
    return {
        "event_id": _new_id(),
        "event_type": event_type,
        "schema_version": schema_version,
        "tenant_id": tenant_id,
        "producer": producer,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "aggregate_version": aggregate_version,
        "sequence_no": sequence_no,
        "occurred_at": occurred_at,
        "emitted_at": emitted_at,
        "correlation_id": correlation_id or _new_id(),
        "causation_id": causation_id,
        "idempotency_key": idempotency_key or f"{event_type}:{_new_id()}",
        "classification": classification,
        "payload_sha256": canonical_hash(payload),
        "payload": payload,
    }
