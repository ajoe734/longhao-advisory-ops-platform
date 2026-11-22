# Finance, AML/CFT, Internal Control and Audit

## Finance lifecycle
Contract/service terms -> invoice/receivable -> payment -> provider settlement/bank reconciliation -> prepayment liability -> revenue recognition by service period under approved accounting policy -> refund/credit adjustment -> GL/ERP export.
Cash receipt, bank settlement and earned revenue are distinct. Commission consumes finance-confirmed eligible untaxed service revenue. Chart of accounts, tax, revenue policy and ERP mapping require accountant/tax advisor approval.

Refund requires eligibility calculation, maker/checker approval, idempotent provider submission, status reconciliation and linked accounting adjustment.

## AML/CFT controls
- Customer identification and verification; applicable agent/beneficial owner identification.
- Purpose/nature of relationship and risk classification.
- Applicable PEP, sanctions and terrorist-financing list screening.
- Possible-match triage and human disposition; confirmed matches escalated under approved procedure.
- Enhanced due diligence for applicable higher-risk cases.
- Ongoing monitoring and periodic/event-driven refresh.
- Suspicious transaction assessment/reporting handled only by designated authorized roles.
- Restricted case access, confidentiality, evidence, retention and independent audit.
- Never disclose restricted STR status/details to sales/customer service.
- Never send restricted AML data to general LLM, Pantheon or marketing.
- Exact legal applicability, reporting thresholds, retention and responsible roles require Taiwan legal/compliance review; do not copy bank-only obligations without confirming applicability.

## Internal control
Maker/checker for acceptance exceptions, refunds, commission overrides, access grants and publication exceptions. Analyst signs only authorized exact report version. Internal audit independent and read-only for evidence. Append-only audit events include actor, role, timestamp, action, before/after refs, reason, correlation ID and policy version. Vendor access least privilege, time-limited and logged.

## Security
Staff MFA; RBAC+ABAC; TLS and at-rest encryption; KMS/key rotation; secret manager; sensitive log redaction; synthetic non-production data; secure SDLC; dependency/container/secret scans; incident response and DR exercises.
