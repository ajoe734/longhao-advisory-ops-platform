# LongGood development instructions

## Mission and scope

Build all fourteen modules defined in `docs/01-product-scope.md`. The requested delivery is the complete operations platform. Start fourteen independent workstreams against the published contracts; integration dependencies must not be turned into serial development gates. Do not relabel the delivery as an MVP or omit finance, AML/CFT, CRM, commission, support or audit.

Read `docs/00-document-index.md`, `agents/PARALLEL_DEVELOPMENT.md` and your task packet before implementation. Latest explicit user decisions take precedence. Record unresolved business choices in `docs/18-decision-register.md`; implement configurable behavior and tests without pretending it is approved for production.

## External boundaries

LegendFlow owns the customer website. Pantheon owns research, Persona and its own trading pools. Agora/Servant supplies research conversations and revisions. Implement adapters only. Their proposed integration endpoints are local contract proposals until a pinned external implementation passes provider contract tests.

## Working agreement

- Use one worktree/branch per module, based on the common contract baseline. Follow the paths in the task packet. Do not reset another agent's work.
- API schemas, event schemas, core migration files, shared UI, lockfiles and CI have explicit stewards. Propose changes through the integration coordinator; merge compatible additions promptly while other modules continue against mocks.
- Business state changes require authenticated command handlers, server-side authorization, expected version, idempotency and audit/outbox atomicity. Database access follows module ownership.
- Customer signature precedes due diligence; company acceptance, effective contract and entitlement are distinct.
- Only an authorized professional may approve/sign a report. A verified final signature causes a deterministic authorized publication service to publish automatically. LLM agents cannot sign, approve or invoke publication authority; this does not prohibit system automation after approval.
- Preserve the exact report, recommendation, approved channel text and audience policy digest. A material revision requires a new review/signature version. Do not backdate research to a Persona trade.
- Do not award commission for assignment alone, nor equate missing weekly contact with seven days of lapsed service. Use versioned policy and finance-confirmed inputs.
- Do not send messages to real clients, initiate real payments/refunds, change production access or launch production integrations from test code. Tests use synthetic identities and fake providers.
- This is a public repository: no real PII, KYC, AML case data, source internal PDFs, secrets, signed private certificates or live payment/card details.

## Completion evidence

Provide changed paths, exact base/head, commands and actual test results, contract compatibility, migration evidence, screen states, negative paths, remaining production settings and implementation status. A mock success is not an external integration pass. A specification pass is not a completed product. Do not close issues #1–#14 solely because documentation has been written.

## Shared delivery gate

All fourteen modules, required end-to-end scenarios, authorization isolation, accounting reconciliation, commission reversals, signature/publication evidence and recovery drills must pass before full release. Partial PR merging is permitted to reduce conflicts; it does not shrink the committed scope.

## Explicit external-repository restriction

The user expressly forbids modifying Pantheon or LegendFlow. Work only in this operations-platform repository: integration specifications, mocks and local adapters are permitted. Do not create commits, PRs, issues requesting implementation, deployments or configuration changes in those external projects. Read-only source inspection is permitted when needed to map contracts. Record unavailable external endpoints as adapter gaps; do not implement them inside the external repositories. The same separation applies to Agora/Servant.
