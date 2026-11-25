# M06 驗收測試

執行：

```bash
python3 -m pytest tests/modules/agora_review -q
```

`test_uat.py` 逐一對應 UAT-M06-001～012；`test_contract_worker_http.py` 驗 7 個 route、RFC 7807/ETag/idempotency headers、
producer（`review.revision_requested`/`review.revision_received`）與 consumer（`research.candidate_validated`/
`research.candidate_superseded`）的 schema/hash/duplicate/sequence-gap/tenant isolation、outbox lease/retry 與
production fake gate；`test_security_automation.py` 驗 tenant isolation、LLM/automation 無簽章發布金流能力、webhook
HMAC 簽章驗證；`test_ui_contract.py` 驗四個畫面入口與八種狀態、degraded 連線與 stale 版本不被默默覆核的文案。

全部 fixture 為 `.invalid`／固定 UUID／local fake（`FakeServant`、`FakeArtifactDiff`、`LocalEventBus`），不會呼叫
真實 Agora、Pantheon 或任何外部網路。

PostgreSQL migration proposal（見 `database/proposals/M06/`）與前端 lint/typecheck 尚需 integration/data steward 配
正式 migration 與共用前端建置管線後執行；本模組測試通過不能宣稱真 Agora/Servant 或 M05/M07 整合已接通。
