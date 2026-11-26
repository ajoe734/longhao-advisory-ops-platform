# M11 驗收測試

執行：

```bash
python3 -m pytest tests/modules/ownership_pool -q
```

（repo 內多個模組的測試檔共用 `test_uat.py` 檔名、且無 `__init__.py`；一起跑
`tests/modules` 全部模組會因 pytest basename 衝突失敗——這是既有跨模組佈局限制，
不是本模組的缺陷。本模組的正式驗證入口一律是上面這條 scoped 指令。）

- `test_uat.py`：逐一對應 UAT-M11-001～012（見 `traceability.md`）。
- `test_consumer.py`：`contract.effective`/`contract.terminated`/`contract.service_ended`
  三個 consumer 的 schema 驗證、payload hash 比對、重送去重、sequence gap retry，以及
  M11 對自己發出的 `ownership.employee_departed` 的防禦性 no-op 確認。
- `test_worker.py`：outbox publisher 的 lease／bounded retry／deadletter，以及
  policy sweep 對同一 idempotency key 不重複套用回收。
- `test_authorization.py`：角色拒絕（403）、CAS 缺失（428）／過期（412）、
  idempotency 重放與衝突（409）、evidence 不存在（422）等正反路徑。
- `test_http.py`：`M11Controller` 的 ETag／Idempotency-Replayed header 與
  RFC 7807 `application/problem+json` 渲染。

全部 fixture 為固定 UUID／`InMemoryRepository`／本地 fake（`FakeEvidencePort`、
`FakeRestrictionPort`、`LocalEventBus`），不會送出真實通知、連外或觸發薪酬發放。

## 已知限制（NOT_CONNECTED / NOT_RUN）

- M10 的 `crm.activity`／`crm.conversion` 唯讀依賴未在本模組接線；UAT-M11-004／011
  只驗證 M11 自身（assignment 不自動給獎金、歷史列不被覆寫），crm.conversion 的
  不可變性是 M10 自己的驗收責任。
- PostgreSQL schema／RLS／`database/proposals/M11/` 尚待 integration/data steward
  套用並在 disposable PostgreSQL 16 執行 `database/tools/verify_database.py`；
  本測試套件完全在記憶體內執行，不代表正式 schema 已驗證通過。
- `apps/admin/src/features/ownership_pool/` 的 TypeScript 僅完成靜態撰寫，本次環境
  無法安裝 npm 依賴（network/package install 超出本任務授權範圍），未執行
  `tsc -b`／`vite build`；需要實際裝好依賴的環境再跑 `pnpm typecheck`。
- HR departure adapter／Pantheon／Agora／LegendFlow 均為外部系統，本模組只用
  `FakeEvidencePort`／`FakeRestrictionPort` 模擬；真實接線需另外在已授權環境驗證。
