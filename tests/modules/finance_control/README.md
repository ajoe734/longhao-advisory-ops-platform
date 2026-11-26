# M14 驗收測試

執行：

```bash
python3 -m pytest tests/modules/finance_control -q
```

`test_uat.py` 逐一對應 UAT-M14-001～012；`test_contract_worker_http.py` 驗 24 個 route、RFC 7807/ETag/idempotency headers、producer event schema、consumer 的 hash/duplicate/sequence-gap/tenant isolation、outbox lease/retry 與 production fake gate；`test_security_automation.py` 驗 cross-tenant isolation、service-identity-only 的 `execute_refund`/webhook 角色門檻、restricted evidence 門檻與最小化 automation；`test_ui_contract.py` 驗四個畫面入口與八種狀態。全部 fixture 為 `.invalid`／固定 UUID／local fake，不會啟動真實金流、退款、會計 ERP 或連外。

PostgreSQL schema 與 `database/proposals/M14` 尚需 integration/data steward 配正式 migration 後，在非 superuser PostgreSQL 16 執行；本模組測試通過不能宣稱真實金流／退款 provider 或會計 ERP 已接通。
