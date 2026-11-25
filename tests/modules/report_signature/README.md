# M07 驗收測試

執行：

```bash
python -m pytest tests/modules/report_signature -q
```

`test_uat.py` 逐一對應 UAT-M07-001～010、012（UAT-M07-011 為 UI 揭露需求，見下）；`test_contract_worker_http.py` 驗 8 個 route、RFC 7807/ETag/idempotency headers、兩個 producer schema（`report.version_frozen`／`report.signature_verified`）、三個 consumer（`research.candidate_superseded`、`review.revision_received`、自身 `report.version_frozen`）的 hash/duplicate/order/tenant、outbox lease/retry、簽章逾時 reconciler；`test_security_automation.py` 驗 maker-checker、tenant isolation、idempotency conflict、CAS 412/428、撤銷重複、webhook service principal、簽署時間窗口與 draft-assist 最小化；`test_ui_contract.py` 驗畫面入口、八種狀態與 UAT-M07-011 的自動發布揭露。全部 fixture 為 `.invalid`／固定 UUID／local fake，不會送出真實簽章、憑證或連外。

PostgreSQL `report.guard_frozen_version`／`report.guard_signature`／`report.require_signature_outbox` trigger 已在 baseline 存在；本模組 `receive_analyst_signature` 的 `signed_at` 窗口檢查、hash 一致性檢查是刻意對齊這些 trigger 的邊界（見 `database/proposals/M07/README.md`）。本模組測試通過不能宣稱真簽章 provider、M05/M06/M08 或 M14 物件儲存已接通；PostgreSQL RLS 與 proposal 尚需 integration/data steward 配正式 migration 後，在非 superuser PostgreSQL 16 執行。

已知的跨模組測試探索限制：`tests/modules/customer_identity_application` 與本目錄各自有同名的 `test_uat.py`／`test_contract_worker_http.py`／`test_security_automation.py`，兩個目錄皆無 `__init__.py`；單獨執行各自目錄（如上指令）沒有問題，但同時收集 `tests/modules` 整棵樹會因 pytest 預設 import mode 對同名模組報 `import file mismatch`。這是沿用 M01 既有檔名慣例後才會出現的根層級 collection 問題，修法在根 `pytest.ini`/`pyproject.toml`（例如 `--import-mode=importlib`）或替每個 module 目錄加 `__init__.py`，屬 integration steward 負責的共用設定，不在本模組 `write_scopes` 內處理。
