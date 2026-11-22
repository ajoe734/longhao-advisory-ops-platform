# LongGood Advisory Operations Platform

This repository builds the **LongGood internal operations platform only**. LegendFlow is the external customer-facing website; Pantheon is the external research/persona/evidence/artifact engine; Agora/Servant is the analyst interaction surface. Do not rebuild those systems here.

## Core operating model
1. Customer signs the application/contract package first.
2. Customer completes online KYC, suitability and applicable AML/CFT information.
3. LongGood performs required review and records company acceptance separately.
4. Contract effectiveness and service entitlement are activated only when all approved conditions hold.
5. Pantheon Persona may autonomously generate research candidates; analysts may also initiate research.
6. Candidates enter LongGood review. Analysts may use Agora/Servant to request revisions.
7. Required professional/compliance reviews complete; analyst signs the exact immutable version.
8. Verified signature triggers automatic publication to eligible LegendFlow customers and approved messaging channels.
9. CRM, service cases, finance, commission and audit evidence share customer/contract/event identifiers.

## Modules
CMS; Contract & Due Diligence; Entitlement; Research Review & Signature; Advisory Publication; CRM & Commission; Customer Service; Finance; Compliance/Internal Control.

## Start here
- docs/01-product-scope.md
- docs/02-SA.md
- docs/03-SD.md
- docs/04-integrations.md
- docs/05-workflows.md
- docs/06-crm-commission.md
- docs/07-finance-compliance-control.md
- agents/AGENTS.md
- qa/uat.md
- docs/adr/README.md

## Important TBDs
Base commission pool rate 3% is a proposal, not approved. Signature provider/type, AML vendors and exact regulatory procedures, tax/accounting mapping, retention periods, SLA and RPO/RTO require owner/legal/accounting approval. Do not invent them.
