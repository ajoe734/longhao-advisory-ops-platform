# HerdR Agent Protocol

## Mission
Implement the LongGood operations platform in this repository. Read README and relevant specs before coding.

## External boundaries
- LegendFlow owns customer-facing website/member UI. Integrate by API/events; do not rebuild.
- Pantheon owns Persona, trading pools, research orchestration, evidence and research artifacts. Integrate; do not rebuild.
- Agora/Servant is the analyst interaction surface. Store references and review evidence; it cannot sign or publish.
- LongGood owns customers, applications, company acceptance, contracts, entitlements, formal advisory reports/signatures, recommendations/publications, CRM, commissions, cases, finance reconciliation and audit.

## Safety
- Never commit customer PII, KYC documents, AML cases, tokens, private keys, certificates, production data or internal-only source files.
- Public repository: use synthetic fixtures only; run secret and PII scans before every commit.
- LLM output is untrusted draft material. It cannot accept customers, clear AML alerts, determine suitability, sign reports, publish, alter entitlements, calculate discretionary awards or release money.
- Restricted AML data must not enter general CRM, customer service, Pantheon or general LLM prompts.
- Enforce MFA, RBAC+ABAC, separation of duties, append-only audit and least privilege.

## Workflow
1. Select one GitHub issue/task packet.
2. Read referenced docs and identify assumptions/TBDs.
3. Write plan and tests.
4. Implement behind ports/adapters; mock unavailable vendors.
5. Run lint, type, unit, integration and security checks.
6. Update API/docs/migrations.
7. Report changed files, test commands/results and remaining risks.

## Definition of Done
Acceptance criteria met; positive/negative/idempotency/authorization tests pass; no unresolved critical/high security findings; audit/observability included; docs updated; no secrets/PII.
