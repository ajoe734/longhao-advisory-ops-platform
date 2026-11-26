# M05 資料庫整合提案

基準：`research.candidate` / `research.candidate_evidence`（`database/migrations/0001_domains.sql`）。本目錄不是已套用 migration；integration/data steward 應配置正式序號、更新 catalog／dictionary／grants，並在 disposable PostgreSQL 16 實跑。

`001_pantheon_intake.sql` 補足基線 `research.candidate` 無法表達的 OpenAPI/事件必要欄位：`external_event_id`（UAT-M05-003 來源去重）、`source_available_at`／`source_committed_at`（與既有 `source_occurred_at` 分別保存，UAT-M05-007）、`ticket_ref`／`plan_ref`／`license_scope_ref`／`data_scope`（CandidateCreate 必填）、`review_case_id`／`validation_failure_codes`（ResearchCandidate 必填回應欄位）、`supersedes_candidate_id`／`superseded_by_candidate_id`（UAT-M05-011 lineage）、`customer_distribution_allowed`（UAT-M05-010）、`validated_snapshot_asset_id`／`conflicting_receipts`（UAT-M05-004 衝突證據，不覆寫原列 `content_sha256`）。新增 unique 索引確保同 `(tenant, producer, external_candidate_id)` 只有一個非 `superseded` 的 lineage head；trigger 鎖定身分與內容事實（producer/external_candidate_id/external_version/external_event_id/content_sha256/received_at/source_occurred_at）不可被 UPDATE 覆寫。`research.candidate_evidence` 欄位與本模組 domain 完全對應，不需變更。

整合步驟：

1. 先確認既有合成 seed 無 `research.candidate` 既存列（本模組尚未實際寫入正式 DB）；若已有，回填 `external_event_id`／`source_available_at`／`ticket_ref`／`license_scope_ref`／`data_scope`。
2. 驗證 `NOT VALID` constraints 後轉為 validated；`ux_m05_candidate_lineage_head` 與 `m05_candidate_external_event_unique` 直接建立（非 `NOT VALID`，因應用層已保證寫入時滿足）。
3. 由資料 steward 授與 `longhao_m05` 最小 DML 權限；M06（review.case_file）、M07（report_version）只透過既有 FK／API 讀取 `research.candidate`，不直接改寫本模組欄位。
4. 執行 `python database/tools/verify_database.py --dsn-env LONGHAO_TEST_DATABASE_URL`，另加同 `external_candidate_id` 兩個非 superseded 列應被唯一索引拒絕、身分欄位 UPDATE 應被 trigger 拒絕的案例。

回復採 forward-fix。已有 superseded lineage 或 conflicting_receipts 證據後不可直接 drop 欄位；應先停寫、匯出可驗證證據，再由核准 migration 做相容降級。

契約協調項見 `contract-changes.md`：`research.candidate.origin`／`status` 的 DB 字面值與 OpenAPI/事件字面值不同，由 `apps/api/src/modules/pantheon_intake/domain.py` 的 `origin_to_wire`/`status_to_wire` 做唯一轉換，兩端契約都不應被本模組私自改字面值。
