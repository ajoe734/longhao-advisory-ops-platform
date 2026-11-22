# Architecture Decision Records

## ADR-001 Modular monolith
Status: Proposed. Build bounded contexts in one deployable core plus durable workers to preserve transaction invariants and reduce operational complexity.

## ADR-002 External system boundary
Status: Accepted product boundary. Pantheon, Agora/Servant and LegendFlow remain external; integrate through versioned adapters/events; no shared DB.

## ADR-003 Signature triggers publication
Status: Proposed pending compliance approval. After required reviews, verified signature of immutable report version emits publication event. Delivery failures do not invalidate signed report.

## ADR-004 Customer signs before due diligence
Status: Product decision; legal wording/procedure pending. Customer signs application/contract package first, then KYC/suitability/AML-CFT; company acceptance, contract effectiveness and entitlement are separate.

## ADR-005 Commission event/revenue based
Status: Proposed. Keep source, maintenance owner and conversion owner distinct. Use finance-confirmed untaxed service revenue net refunds. 3% pool rate is not approved.
