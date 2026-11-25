# M08 模組測試

```bash
python3 -m pytest tests/modules/publication -q
```

`conftest.py` 以 `InMemoryRepository` 與 `Fake*` adapters 組裝 `PublicationService`，使用固定測試
clock（`FixedClock`）讓簽章時間、建議建立時間與發布時間可被精確比較。`test_uat.py` 覆蓋
UAT-M08-001～012 及 CAS／idempotency／授權拒絕；`test_contract_worker_http.py` 驗 OpenAPI/event schema、
controller headers、consumer 的 hash/tenant/sequence 檢查、outbox lease/retry 與 production fake-adapter
guard。

跨模組測試目錄缺少 `__init__.py`，在 pytest `prepend` import 模式下與其他模組的同名測試檔
（例如 `test_uat.py`）會互相衝突；因此驗證指令固定為 `tests/modules/publication`（任務包第9節給定的命令
範圍），不是 `pytest tests/` 全樹執行。這是既有跨模組測試樹結構限制，不是本次新增的回歸。
