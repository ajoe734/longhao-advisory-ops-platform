# M03 驗收測試

執行：

```bash
python -m pytest tests/modules/kyc_suitability -q
```

`test_uat.py` 逐一對應 UAT-M03-001～012；`test_contract_worker_http.py` 驗 8 個 route、RFC 7807/ETag/idempotency headers、2 個 producer schema（`kyc.decision_recorded`、`suitability.assessed`）、2 個 consumer 事件（`application.customer_signed`、`customer.profile_updated`）的 hash/duplicate/order、outbox/document-scan worker 的 lease/retry 與 production fake gate。全部 fixture 為合成 UUID／local fake，不會呼叫真實身分驗證、掃毒供應商或外部系統。

執行 `tests/modules/kyc_suitability` 單獨可乾淨通過；與其他模組測試目錄一起跑 `pytest tests/` 時，因各模組 `tests/modules/*` 皆無 `__init__.py`、檔名重複（例如 `test_uat.py`），pytest 會回報 `import file mismatch`——這是既有跨模組測試版面問題，不在本模組 `write_scopes` 內，需由 integration steward 統一調整（例如全面加 `__init__.py` 或改用 `--import-mode=importlib`），不應由單一模組私改共用測試配置。

PostgreSQL RLS 與 `database/proposals/M03` 尚需 integration/data steward 配正式 migration 後，在非 superuser PostgreSQL 16 執行；本次只以 `pglast` 做語法解析驗證（`parse_sql` 成功），未連實際 PostgreSQL 執行 constraint/RLS 測試。本模組測試通過不能宣稱真 Pantheon/Agora/LegendFlow/掃毒供應商已接通。
