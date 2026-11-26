# M13 客服客訴驗收測試

執行：

```bash
python -m pytest tests/modules/service_cases -q
```

`test_m13_uat.py` 逐一對應 UAT-M13-001～012；`test_m13_contract_worker_http.py` 驗 14 個
route、RFC 7807/ETag/idempotency headers、四個 producer schema（`support.case_opened`
/`support.case_escalated`/`support.case_resolved`/`support.response_status_changed`）、
consumer 的 hash/duplicate/sequence-gap/tenant isolation（含本模組自produce的
`support.case_opened`、`ownership.assignment_changed` released/departure/superseded
的正確投影）、outbox 與 response-dispatch worker 的
lease/bounded-retry/unknown-result-lookup（狀態只落在 queued/unknown/failed，
不寫入非合約列舉值 retry）、webhook Idempotency-Key/timestamp/nonce/signature
驗證、router prefix 不重複、以及 production fake adapter gate；
`test_m13_security_automation.py` 驗 tenant isolation、
customer 只能記 inbound 互動、電話/現場需錄音同意證據、drafting assistant 只收最小化
輸入、AML 升級案件完全不呼叫 drafting assistant、contains_new_investment_judgment
的硬性拒絕核定/送出、`approved_low_risk_template` 僅受控 policy service 可用且排除
complaint/refund/research_question/AML、allowed_actions 不重複對已結案提供 resolve
也不對草稿人提供 approve，以及本模組沒有任何退款 approve/execute 方法；
`test_m13_ui_contract.py` 驗三個畫面入口、八種 UI 狀態、未驗證案件隱藏契約/付款連結、
內部筆記與對外回覆分區、以及送達狀態不可冒充已送達。

測試檔名加 `m13_` 前綴以避免與其他模組（例如 M01）在 pytest rootdir-relative
匯入模式下的 basename 衝突；共用輔助函式一律以
`from tests.modules.service_cases.conftest import ...` 完整路徑匯入。

全部 fixture 為 `.invalid`／固定 UUID／local fake（`FakeObjectAssetStore`、
`FakeKnowledgeBase`、`FakeChannelBindings`、`FakeSuppressionRequests`、
`LocalDraftingAssistant`、`LocalEventBus`、`LocalMessagingProvider`），不會傳訊、
出金或連外；M10 suppression intake 與客服回覆通路 provider 均為 NOT_CONNECTED，
僅驗證本平台 contract mock。

PostgreSQL RLS 與 `database/proposals/M13/` 尚需 integration/data steward 配正式
migration 後，在非 superuser PostgreSQL 16 執行；本模組測試通過不能宣稱真
LegendFlow／官方訊息／email／電話 provider 或 M10／M14 已接通。
