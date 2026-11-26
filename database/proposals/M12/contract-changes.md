# M12 契約協調與逐項依據

比較基準：`database/migrations/0001_domains.sql`／`0002_isolation_indexes.sql`／`0003_business_guards.sql`（dev baseline）
對照 `apps/api/src/modules/commissions/{domain.py,service.py,calculation.py}`（本次實作，已通過 `tests/modules/commissions`）。

## 1. 欄位缺口（baseline 表存在，但實作讀寫的欄位不存在）

| 表 | 新欄位 | 依據（程式位置） | 理由 |
| --- | --- | --- | --- |
| `commission.policy_version` | `effective_until` | `domain.py` `CommissionPolicy.effective_until` | OpenAPI/task packet 的政策可有迄日。 |
| | `age_anchor`, `year3_assisted_marketing_rule` | `domain.py` `UNRESOLVED_POLICY_FIELDS`；`service.py` L124-129 驗證值域 | 只擋 live（`live_eligible()`），不擋核准；baseline CHECK 只硬性要求 `decay_interaction`/`tail_lineage_rule`（對映 `allocation_interpretation`/`service_lineage_rule`）已解析，與本實作的核准門檻一致，但這兩欄本身沒有儲存位置。 |
| | `pool_policy_version`, `recognition_policy_version` | `service.py` L108-143，`create_commission_policy` 的 required fields | 政策提案必須引用的外部版本標記，無對應欄位。 |
| | `rounding_mode`, `rounding_scale` | `domain.py` `apply_rounding`；`service.py` L130-134 | 決定結算四捨五入，UAT-M12-010「金額分配總數守恆」依賴此欄位固定，不能每次計算自選。 |
| | `full_policy_asset_id` | `domain.py` `CommissionPolicy.full_policy_asset_id`（`service.py` L171 `new_id()` 產生） | 完整政策文件版本的物件資產參照。 |
| | `notes` | `service.py` L144-146 | 提案備註，4000 字上限。 |
| `commission.calculation_run` | `policy_sha256` | `service.py` L298 `policy_sha256=policy.content_hash()` | 核算當下政策快照 hash，UAT-M12-006「衰減兩算法不能混用」要求可追查 policy hash。 |
| | `failure_code` | `domain.py` `CommissionRun.failure_code` | `status='failed'` 時的失敗原因；目前 worker 尚無寫入路徑，但資料模型需要容納。 |
| | `currency` | `service.py` L678 `run.currency = currency`（worker 計算時設定） | 本批次幣別；見下方「2. 既有 trigger 參數調整」，此欄位會在 `queued→calculated` 轉換時被改寫。 |
| `commission.adjustment` | `original_recipient_id` | `domain.py` `CommissionAdjustment.original_recipient_id`；`service.py` L482 複製 `original.beneficiary_actor_id` | REQ-M12-007「退款反向原受款人原政策」的直接證據欄；UI／稽核需要顯示且不可由現任維護人推斷。 |
| | `exceeds_original_award` | `service.py` L467 `exceeds = abs(amount) > original.award_amount` | 決定 `recovery_method`／`payroll_offset_allowed`（`domain.py` `to_api`）的輸入依據，須留存而非每次重算。 |
| `commission.payout_batch` | `payroll_mapping_version` | `service.py` L523-525，`create_commission_payout_batch` required field | 批次建立時指定、`control_sha256` 不含此欄但需留存審計。 |
| | `control_sha256` | `service.py` L552（preliminary）、L762（assembly 後覆寫） | `approve_commission_payout_batch`／`acknowledge_commission_payout_batch` 的完整性比對鍵；見下方 run_ids/ack 說明。 |
| | `export_asset_id` | `domain.py` `PayoutBatch.export_asset_id`；`service.py` L657 `batch.export_asset_id = evidence_id` | acknowledge 時記錄的回執證據資產。 |
| `commission.payout_line` | `accepted` | `domain.py` `PayoutLine.accepted`；`service.py` L647-651 | 逐列回執結果（`true`/`false`/`null`），UAT-M12-011「部分成功不重複發放」需要逐列持久狀態，不能只看批次整體 status。 |

## 2. 新增子表（既有欄位無法表達集合關係）

- **`commission.payout_batch_run`**：`PayoutBatch.run_ids`（`domain.py`）是建立時固定的 tuple，baseline 沒有對應的 junction，且既有 `commission.calculation_run` 沒有 `payout_batch_id` 反向欄位（一個 run 理論上可能被多個試算批次引用後才真正入帳，不適合在 run 上加單一 FK）。新表為 append-only（`reject_evidence_mutation`），建立後不可改列。
- **`commission.payout_batch_ack`**：`acknowledge_commission_payout_batch`（`service.py` L611-651）目前只在 `batch.ack_event_ids`（記憶體 tuple）去重，UAT-M12-011 要求「有可驗證且去重的付款回執才標paid」在重啟後仍需成立；新表以 `(tenant_id, batch_id, event_id)` UNIQUE 提供持久化去重證據，append-only。

## 3. 既有 trigger 參數與已實作流程衝突（追蹤到具體程式行為後確認，非假設）

`0003_business_guards.sql` 的 `core.guard_fixed_record(mutable_columns)` 會擋下「不在允許清單」欄位的任何 UPDATE。逐行核對本模組兩個會员 UPDATE 已存在列的流程後，發現目前的允許清單會擋下已實作、已通過測試的業務邏輯本身（測試用的是 in-memory store，不會觸發這個 PostgreSQL trigger，所以沒有在單元測試中顯現）：

- **`immutable_commission_terms`（`commission.policy_version`）**：baseline 允許清單是 `{approval_status,approved_by,approved_at}`。但 `approve_commission_policy`（`service.py` L211-235）在核准時執行 `policy.base_pool_rate = approved_rate` 與 `policy.effective_from = effective_from`——這是任務包「approve_commission_policy：核准池率…」的設計本意（提案時可留 `base_pool_rate=null`，核准時才定案），不是程式錯誤。若不調整允許清單，這個已核准的業務流程在真實 PostgreSQL 上會因 trigger 擲出 `immutable payload or calculation identity` 而失敗。**修正（第二輪退修 F5）**：單純把兩欄加進 `core.guard_fixed_record` 允許清單會造成不可逆regression——`core.guard_policy_approval()` 只鎖 `approval_status`/`approved_by`/`approved_at`，approved 之後 `base_pool_rate`/`effective_from` 仍可被改。本提案改用專用 trigger function `commission.guard_policy_version_terms()`（001_commissions.sql L198-213）：只有 `OLD.approval_status='draft'` 時才允許這兩欄變更，其餘欄位行為與 `guard_fixed_record` 相同；核准後兩欄與其他業務不變量一樣不可逆。
- **`immutable_calculation`（`commission.calculation_run`）**：baseline 允許清單是 `{status,completed_at,approved_by,approved_at}`。但 `run_calculation_job`（`service.py` L665-726，worker 的計算進入點）在 `queued→calculated` 轉換時執行 `run.currency = currency` 與 `run.input_sha256 = canonical_hash(...)`，兩者在建立時分別只是預設值 `"TWD"`／空字串，計算完成後才是真正的快照值。本提案把 `input_sha256`、`currency` 加入允許清單；同時把新增的 `failure_code` 一併加入，因為失敗路徑同樣是建立之後才知道原因碼（目前 worker 尚無寫入路徑，資料模型先留欄位）。

兩項調整都**不放寬**既有的業務不變量本身（maker≠checker、policy 核准門檻、run 核准門檻皆原樣保留），只放寬「這個已核准流程被允許touch的欄位集合」，且已確認没有其他程式路徑會利用放寬後的欄位做未授權覆寫（`policy_version`／`calculation_run` 的其餘欄位仍受保護）。

- **`commission.entry` baseline CHECK 衝突（第二輪退修 F5）**：baseline `CHECK (beneficiary_type <> 'sales' OR beneficiary_actor_id IS NOT NULL)` 會擋下 `run_calculation_job`（`service.py` L706-730）為自然續約/無協助業務認列產生的 `beneficiary_type='sales'`、`beneficiary_actor_id=NULL`、`award_amount=0` 佔位列（UAT-M12-002 斷言 `by_type["sales"]["recipient_id"] is None`）。本提案以 `m12_entry_sales_actor_or_zero_ck`（001_commissions.sql L229-245）取代原 CHECK：只有 `award_amount=0` 的業務列可以沒有受款人，非零業務獎金仍強制要有受款人，不放寬原有保護意圖。
- **`commission.guard_live_run()` 缺 `age_anchor`/`year3_assisted_marketing_rule` 檢查（第二輪退修 F5）**：`CommissionPolicy.live_eligible()`（`domain.py` L227-228）與 `create_commission_run` 的 `policy_not_approved` 檢查（`service.py` L275）都要求這兩個新欄位已解析才能進 live run，但 baseline trigger 寫在這兩欄新增之前，只檢查 `decay_interaction`/`tail_lineage_rule`。本提案以 `CREATE OR REPLACE FUNCTION commission.guard_live_run()`（001_commissions.sql L257-266）補上這兩項檢查，維持 DB 層與應用層門檻一致，避免未來繞過應用層驗證時仍能插入 live run。

## 4. 未決事項（本提案不自行決定，留 steward／decision register 核定）

- **ENG-COM-01**：`create_commission_adjustment` 只有一個 API 命令，OpenAPI 未定義獨立覆核端點，無法強制兩位 staff 分別扮演 maker/checker。`commission.adjustment` 的 baseline CHECK 是 `CHECK (prepared_by <> approved_by)`（強制兩人）。但 `create_commission_adjustment`（`service.py` L462-466）在沒有 `finance.refund_confirmed` 事件預先建立 `PendingAdjustmentDraft` 時，會落到 `prepared_by, approved_by = context.actor_id, context.actor_id`（同一人）的簡化人工路徑——這條路徑若寫入真實 PostgreSQL 會直接違反此 CHECK。本提案**保留**既有 CHECK 不變（不擅自放寬兩人強制），因為放寬與否是業務授權範圍的決定，不是程式修正；在 steward 核定前，這條單一控制路徑只能停留在本模組目前的 in-memory 測試環境，不能視為已可接 PostgreSQL 生產。此條目的解法方向（契約補上獨立覆核端點，或正式核准目前簡化規則）待 Supervisor／steward 決定是否擴充本 task 的 `write_scopes` 以納入 `docs/18-decision-register.md` 後再登記；本 task 的 `write_scopes` 不含 `docs/`，故暫不直接編輯該檔。
- **`refund_id`（app，必填，`service.py` L434 `require_uuid(body["refund_id"], ...)`）與既有 `refund_allocation_id`（baseline，nullable，FK 指向 `finance.refund_allocation`）是否為同一識別碼**：本模組未對 `refund_id` 查證任何 M14 port（對照 `evidence_asset_id` 會呼叫 `self.evidence.exists(...)`，`refund_id` 沒有對等檢查），因此無法由程式行為確認兩者等價。本提案沿用既有 `refund_allocation_id` 欄位但不緊縮為 NOT NULL，待財會/steward 確認語意後再提送收緊提案；若確認不是同一概念，需另開欄位而非覆寫。
- **`commission.adjustment.approved_at`**：baseline 為 `NOT NULL`，但 `domain.py` `CommissionAdjustment` 沒有獨立建模此欄（調整在單一命令內直接產生，`created_at`/`updated_at` 都是同一個 `now`）。Repository/mapper 層需在持久化時以 `approved_at = created_at` 填入；待 ENG-COM-01 核定兩人覆核後，這裡應改為覆核時另行寫入的真實時間，不是建立時間的複製。

## 5. Mapper 對照（補充 `database/README.md` 既有表）

| API／內部欄位 | 資料層 |
| --- | --- |
| `CommissionPolicy.allocation_interpretation` | `commission.policy_version.decay_interaction` |
| `CommissionPolicy.service_lineage_rule` | `commission.policy_version.tail_lineage_rule` |
| `CommissionRun.policy_id` | `commission.calculation_run.policy_version_id` |
| `PayoutBatch.run_ids` | `commission.payout_batch_run`（新增 junction，見上） |
| `PayoutBatch.ack_event_ids` | `commission.payout_batch_ack`（新增，見上） |
