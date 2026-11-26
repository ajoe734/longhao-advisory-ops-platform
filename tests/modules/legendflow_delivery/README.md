# M09 驗收測試

執行：

```bash
python -m pytest tests/modules/legendflow_delivery -q
```

`test_uat.py` 逐一對應 UAT-M09-001～012；`test_contract_worker_http.py` 驗 8 個
operationId 的 idempotency-key／If-Match／pagination 契約、webhook 簽章與時間窗、
9 個事件 consumer 的 schema／hash／duplicate／sequence-gap，以及
`DispatchWorker`／`OutboxPublisher` 的 lease/retry/dead-letter；
`test_security_automation.py` 驗 SoD、跨 member 資源隔離與
`delivery_exception_summary` LLM 能力邊界；`test_ui_contract.py` 驗畫面入口與
八種狀態。全部 fixture 為 `.invalid`／固定 UUID／本機 fake，不會對真實
LegendFlow、LINE/SMS/email provider 或外部系統送出任何請求。

PostgreSQL RLS 與 `database/proposals/M09` 尚需 data steward 配正式 migration
後，在非 superuser PostgreSQL 16 執行 `database/tools/verify_database.py`；本模組
測試通過不能宣稱真 LegendFlow 或跨模組 provider 已接通。見 `traceability.md`
已知限制一節（跨模組同名測試檔 collection 限制、`OutboxPublisher` 真實時鐘）。
