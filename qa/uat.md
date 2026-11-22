# UAT Acceptance Scenarios

Use synthetic data only. Record expected and actual result, actor, timestamps and audit evidence.

1. Customer signs before KYC; due diligence begins and entitlement remains locked.
2. Incomplete KYC can be resumed; prior answers/version remain traceable.
3. Company rejects after payment; entitlement remains locked and refund workflow starts.
4. AML possible match creates restricted case; sales/support cannot view details; no automatic clearance.
5. Suitability conditional/not suitable follows configured human decision and approved policy.
6. Pantheon Persona candidate arrives without analyst-created ticket; review case created with artifact/evidence refs.
7. Analyst-initiated candidate enters same review queue.
8. Analyst requests Servant revision; new artifact version linked, old snapshot retained.
9. New Pantheon artifact arrives during review; old review marked stale.
10. Missing review or invalid analyst authority blocks signature/publication.
11. Valid signature verifies exact hash and emits publication event automatically.
12. Audience snapshot excludes customers without effective eligible entitlement.
13. Messaging timeout triggers status lookup/retry without duplicate dispatch.
14. Correction/withdrawal creates new version; old publication evidence retained.
15. Marketing-origin auto-renewal without sales activity creates no sales commission.
16. Evinced sales-led renewal follows approved 30/70 policy version.
17. Seven days without qualifying activity returns eligible relationship to Company Pool; active service remains contract-governed.
18. Employee departure immediately transfers maintenance assignment; historical commission unchanged.
19. Multiple concurrent contracts: ending one does not remove other entitlements.
20. Refund creates linked revenue and commission adjustment.
21. Duplicate payment webhook creates one payment/accounting event.
22. Duplicate research candidate event creates one review case.
23. Customer switches support channel; same case is continued/linked.
24. Auditor retrieves customer/report/message chain with source, review, signature, audience and delivery evidence.
25. Vendor/admin access is time-limited and logged; production PII excluded from general research context.
