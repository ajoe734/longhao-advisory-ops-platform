# M05 Pantheon 候選接收驗收測試

執行：

```bash
python -m pytest tests/modules/pantheon_intake -q
```

`test_uat.py` 逐一對應 UAT-M05-001～012；`test_contract_worker_http.py` 驗 5 個 route、RFC 7807/ETag/idempotency headers、3 個 producer event schema、self-consumer 的 hash/duplicate/sequence-gap/tenant 隔離、outbox lease/retry 與 production fake gate；`test_security_automation.py` 驗 tenant isolation、SoD（pantheon_adapter 僅能ingest、research_intake_service 僅能validate、analyst_reviewer 僅能reject）、terminal candidate 不可再變更但保留歷史，以及最小化 triage automation。全部 fixture 為 `.invalid`／固定 UUID／local fake，不會對外發請求、連金流或連真實 Pantheon/Agora。

`ports.py` 的 `SsrfSafeEvidenceFetcher` 以 `FakeEvidenceTransport` 注入 DNS／response／redirect，實測拒絕非 allowlist host、私有/metadata 位址與惡意 redirect（UAT-M05-009），不是只比對常數。

PostgreSQL 新增欄位與 trigger（`database/proposals/M05/001_pantheon_intake.sql`）尚需 integration/data steward 配正式 migration 後，在非 superuser PostgreSQL 16 執行；本模組測試通過不能宣稱真 Pantheon/Agora/M06/M09 已接通，見 `database/proposals/M05/contract-changes.md` 的整合風險清單。

前端 `apps/admin/src/features/pantheon_intake` 的 `tsc -b` typecheck 本次未執行：本機無 `node_modules`，且本 session 的工具權限政策拒絕網路安裝（network-download / host-toolchain-cache 變更）；交由有網路權限的環境或 CI 執行 `pnpm --filter longhao-advisory-ops-admin typecheck`。
