# M01 驗收測試

執行：

```bash
python -m pytest tests/modules/customer_identity_application -q
```

`test_uat.py` 逐一對應 UAT-M01-001～012；`test_contract_worker_http.py` 驗 14 個 route、RFC 7807/ETag/idempotency headers、五個 producer schema、M02/M03/M04/M11 consumer 的 hash/duplicate/order、gate record IDs、assignment aggregate-version 亂序、`application.company_decided` 未簽署拒絕、`ownership.employee_departed` aggregate_id 檢查／dedup／跨租戶隔離、申請佇列的篩選／cursor／401／403／tenant isolation、outbox lease/retry/dead-letter/unknown-result lookup、`TaskRunner` lease/redrive guardrails、FastAPI router 預設 prefix 與 `If-Match`/`marketing_source_ref` 輸入驗證，以及 production fake gate；`test_ui_contract.py` 驗畫面入口與八種狀態。全部 fixture 為 `.invalid`／固定 UUID／local fake，不會傳訊、付款、退款或連外。

PostgreSQL RLS 與 proposal 尚需 integration/data steward 配正式 migration 後，在非 superuser PostgreSQL 16 執行；本模組測試通過不能宣稱真 LegendFlow 或跨模組 provider 已接通。
