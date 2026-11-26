# M11 驗收測試

執行（本模組）：

```bash
python3 -m pytest tests/modules/ownership_pool -q
```

執行（repo 全部模組，包含本模組）：

```bash
python3 -m pytest -q
```

`test_uat.py` 已改名為 `test_m11_uat.py`，解決了與
`tests/modules/customer_identity_application/test_uat.py` 的 pytest basename
衝突（舊版 repo 根目錄 `python3 -m pytest` 會因 "import file mismatch" 直接
collection 失敗，exit 2）；上面兩條指令現在都能跑。

- `test_m11_uat.py`：逐一對應 UAT-M11-001～012，含兩筆 UAT-M11-009 的補充案例
  （入池後才被停權；全部服務仍 active 但已停權）（見 `traceability.md`）。
- `test_consumer.py`：`contract.effective`/`contract.terminated`/`contract.service_ended`
  三個 consumer 的 schema 驗證（含 classification／UUID 格式）、payload hash 比對、
  重送去重、sequence gap retry、`contract.service_ended` 對多個
  `contract_service_id` 的獨立追蹤（修正舊版單一欄位互相覆寫的 bug）、公司Pool
  baseline 傳遞 inbound correlation/causation id、入池當下即停權客戶的建立，以及
  M11 對自己發出的 `ownership.employee_departed` 的防禦性 no-op 確認。
- `test_worker.py`：outbox publisher 的 lease／bounded retry／deadletter／人工重驅，
  `TaskRunner` 的 lease／complete／fail／重驅（可注入 clock），以及 policy sweep
  對同一 idempotency key 不重複套用回收。
- `test_authorization.py`：角色拒絕（403）、CAS 缺失（428）／過期（412）、
  idempotency 重放與衝突（409）、evidence 不存在（422）、離職員工被撤銷
  claim/assign 操作權、授權失敗仍寫拒絕 audit、`evaluate_company_pool` 不重複寫
  audit，以及跨租戶隔離等正反路徑。
- `test_http.py`：`M11Controller` 的 ETag／Idempotency-Replayed header、RFC 7807
  `application/problem+json` 渲染、客戶無任何既有列時首次 assign 經 HTTP 可達，
  以及 idempotency replay 保留原 ETag。
- `test_automation.py`：第11節 LLM 接手摘要／聯絡重點 assistant
  （`apps/api/src/modules/ownership_pool/automation.py`）——untrusted draft、
  model/prompt/ruleset 版本與 source_refs、人工審閱紀錄、最小化輸入、角色限制、
  production fake guard，以及「完全不觸碰 ownership 狀態」的邊界確認。
- `test_admin_ui_contract.py`：對 `apps/admin/src/features/ownership_pool/` 的純
  文字靜態核對（非行為測試，見下「已知限制」）。

全部 fixture 為固定 UUID／`InMemoryRepository`／本地 fake（`FakeEvidencePort`、
`FakeRestrictionPort`、`LocalEventBus`、`LocalHandoffAssistant`），不會送出真實通知、
連外或觸發薪酬發放。

## 已知限制（NOT_CONNECTED / NOT_RUN）

- M10 的 `crm.activity`／`crm.conversion` 唯讀依賴未在本模組接線；UAT-M11-004／011
  只驗證 M11 自身（assignment 不自動給獎金、歷史列不被覆寫），crm.conversion 的
  不可變性是 M10 自己的驗收責任。
- PostgreSQL schema／RLS／`database/proposals/M11/` 尚待 integration/data steward
  套用並在 disposable PostgreSQL 16 執行 `database/tools/verify_database.py`；
  本測試套件完全在記憶體內執行，不代表正式 schema 已驗證通過。提案 SQL 本輪已用
  `pglast.parse_sql()` 做純語法靜態檢查（PASS），不等同實際套用驗證。
- `apps/admin/src/features/ownership_pool/` 的 TypeScript 僅完成靜態撰寫，本次環境
  無法安裝 npm 依賴（network/package install 超出本任務授權範圍），未執行
  `tsc -b`／`vite build`；`test_admin_ui_contract.py` 只做純文字字串核對（例如
  「這個檔案的原始碼裡有沒有出現 `state.kind === "stale"`」），不執行 React、
  不驗證型別、不驗證實際渲染結果。需要實際裝好依賴的環境再跑
  `pnpm typecheck`／瀏覽器互動測試。
- HR departure adapter／Pantheon／Agora／LegendFlow 均為外部系統，本模組只用
  `FakeEvidencePort`／`FakeRestrictionPort`／`LocalHandoffAssistant` 模擬；真實
  LLM provider 與其他外部接線需另外在已授權環境驗證，維持 `NOT_CONNECTED`。
