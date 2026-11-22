# External Integration Contracts

## Principles
Versioned schemas, signed webhooks, correlation IDs, idempotency keys, least privilege, adapter isolation and synthetic contract tests. No shared databases.

## Pantheon candidate event (conceptual)
~~~json
{
  "event_id": "uuid",
  "event_type": "research.candidate.ready",
  "schema_version": "1.0",
  "producer": "pantheon",
  "correlation_id": "uuid",
  "payload": {
    "candidate_id": "string",
    "origin": "persona_autonomous|analyst_initiated",
    "persona_ref": "string|null",
    "pool_ref": "string|null",
    "research_ticket_ref": "string",
    "research_plan_ref": "string|null",
    "artifact_ref": "string",
    "artifact_version": "string",
    "artifact_hash": "sha256",
    "evidence_bundle_refs": ["string"],
    "created_at": "RFC3339",
    "data_scope": "research_only"
  }
}
~~~

LongGood validates schema, source, hash and references, snapshots candidate and creates review case. Candidate never publishes directly. Revision requests reference candidate/artifact version, requested changes, evidence requirements, analyst actor and Servant conversation. Pantheon returns a new artifact reference; never overwrite.

## Agora/Servant
Launch/resume conversation for review case. Provide only scoped research references. Persist conversation/session ref, analyst request, response, revision and artifact lineage. Servant is not signature authority.

## LegendFlow
LongGood exposes minimum-necessary application/contract status, entitlement, approved publication feed, scoped report access and case APIs. LegendFlow sends account binding, application/signature/KYC submission references, payment results and customer case events. LegendFlow cannot assert company acceptance, AML clearance or entitlement.

## Signature provider
Return signer assurance, signed file/reference, certificate/evidence, timestamp, document hash and verification. Provider/type TBD after legal/compliance approval.

## Screening provider
Separate identity verification from PEP/sanctions/terrorist-financing screening. Restricted match details only in AML domain. Store list/provider version, result, reviewer and disposition.

## Messaging
Dispatch only from signed publication. Record provider ID, recipient ref, channel, accepted/sent/delivered/failed states. Idempotency key per publication-recipient-channel.

## Payment/accounting
Authenticate and deduplicate webhooks. Reconcile provider transaction to bank settlement. Accounting export carries source IDs, service period, tax/account mapping version, amounts, debit/credit and export status. Exact schemas TBD.

## Adapter contract tests
Valid event, invalid signature, unknown schema, duplicate, out-of-order, timeout, retry, dead-letter and replay.
