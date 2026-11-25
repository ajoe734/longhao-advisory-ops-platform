# M07 驗收測試

執行：

```bash
python -m pytest tests/modules/report_signature -q
```

本目錄測試檔案一律以 `test_m07_` 前綴命名（`test_m07_uat.py`／`test_m07_contract_worker_http.py`／
`test_m07_security_automation.py`／`test_m07_ui_contract.py`），因為 `tests/modules/customer_identity_application`
（M01）原本就有同名的 `test_uat.py`／`test_contract_worker_http.py`／`test_security_automation.py`／
`test_ui_contract.py`，兩個 module 目錄都沒有 `__init__.py`；同時收集整棵 `tests/modules` 樹時 pytest 預設
import mode 會對同名模組報 `import file mismatch`。前綴後 `python -m pytest tests/modules -q` 可完整收集並
通過本模組與 M01 全部測試（已驗證，不需要改根層 `pytest.ini`/`pyproject.toml` 或替每個 module 目錄補
`__init__.py`）。`conftest.py` 維持原名——pytest 的 conftest 載入不受這個同名限制影響，兩個目錄的
`conftest.py` 可以共存。

`test_m07_uat.py` 逐一對應 UAT-M07-001～010、012（UAT-M07-011 為 UI 揭露需求，見下），並額外驗
`bundle_manifest_sha256` 對報告內容與有效期限的敏感性（UAT-M07-002 延伸）、`freeze_report_version`
鏡像 `report.guard_signature` 的未解決覆核否決檢查與 `required_review_ids` 重複/跨版本拒絕（REQ-M07-001
延伸）；`test_m07_contract_worker_http.py` 驗 8 個 route、RFC 7807/ETag/idempotency headers、三個 producer
schema（`report.version_frozen`／`report.signature_verified`／`report.signature_revoked`）、三個 consumer
（`research.candidate_superseded`、`review.revision_received` 的 stale/not-stale 兩分支、自身
`report.version_frozen`）的 hash/duplicate/order/tenant、outbox lease/retry/過期 lease 回收/dead-letter 與
audited redrive、簽章逾時 reconciler 的查證而非盲重簽、manual-review 去重與 provider lookup timeout 的
skip；`test_m07_security_automation.py` 驗 maker-checker、tenant isolation、idempotency conflict、CAS
412/428、`request_analyst_signature` idempotent replay 在狀態已變化後仍成功且保留 ETag、CAS 早於 provider
session 建立（不洩漏 session）、撤銷重複、webhook service principal、`receive_analyst_signature` 的
`signed_at` 窗口（含未來時間）、`provider_session_ref`／`signer_subject_ref` 冒用、驗證時重查 superseded
candidate、UUID／sha256 格式驗證、quarantine 告警可達與 draft-assist 最小化；`test_m07_ui_contract.py` 驗
畫面入口、八種狀態與 UAT-M07-011 的自動發布揭露。全部 fixture 為 `.invalid`／固定 UUID／local fake，不會
送出真實簽章、憑證或連外。

PostgreSQL `report.guard_frozen_version`／`report.guard_signature`／`report.require_signature_outbox` trigger 已在 baseline 存在；本模組 `receive_analyst_signature` 的 `signed_at` 窗口檢查、hash 一致性檢查是刻意對齊這些 trigger 的邊界（見 `database/proposals/M07/README.md`）。本模組測試通過不能宣稱真簽章 provider、M05/M06/M08 或 M14 物件儲存已接通；PostgreSQL RLS 與 proposal 尚需 integration/data steward 配正式 migration 後，在非 superuser PostgreSQL 16 執行。
