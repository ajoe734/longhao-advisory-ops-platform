# M12 獎金與結算模組

此目錄實作 OpenAPI 1.0.0 的 12 個 M12 operationId。`domain.py` 定義實體、decimal/四捨五入
工具與錯誤；`calculation.py` 是純函式計算引擎（無 I/O），同時供 `preview_commissions`
與 run worker 重用，確保「試算」與「正式」用同一套規則；`service.py` 是 command/query
application service；`http.py` 提供 framework-neutral controller 及可掛載的 FastAPI
router；`ports.py` 的 fake 只接受合成 fixture，production composition 會拒絕 fake adapter；
`automation.py` 是唯讀的差異說明能力，沒有金額決定權或命令權。

## 架構選擇：為何不直接掛 `longhao_platform` 的 `CommandBus`/`InMemoryAtomicStore`

`apps/api/src/longhao_platform/store.py` 目前只支援「單一 aggregate 查找」
（`aggregate(tenant_id, aggregate_type, aggregate_id)`），沒有「依 tenant/類型列出所有列」
的查詢能力。`list_commission_entries`、`get_commission_run` 的彙總、`create_commission_payout_batch`
的批次組裝都需要跨多個 entry/run 列的查詢，而本模組 write_scopes 不包含
`apps/api/src/longhao_platform/`，不能自行擴充共用 store。因此沿用 M01
（`customer_identity_application`）已驗證的自有 `InMemoryRepository`/`StoreState`
模式；錯誤型別、CAS、idempotency、audit、outbox 的語意與共用平台一致（可對照
`longhao_platform/types.py`、`store.py`），日後 store 補上 list 查詢後可切換為共用實作。

## 關鍵業務規則與其程式位置

- 政策未核准不能正式計獎（`policy_not_approved`）：`CommissionPolicy.live_eligible()` +
  `CommissionService.create_commission_run`。核准本身只需要 `allocation_interpretation`
  與 `service_lineage_rule` 已解析；`age_anchor`、`year3_assisted_marketing_rule`、
  `base_pool_rate` 可在核准後仍為 unresolved——這些只擋 **live**，不擋 **policy 核准**
  （對照 `database/data-dictionary.md` 的 DB CHECK 僅硬性要求前兩者）。
- 100/50/25 與 30/70、衰減兩算法（`marketing_decay_only` / `whole_pool_decay`）：
  `calculation.compute_split()`；兩者結果互斥記錄於 `rule_trace_codes`，conservation
  （`marketing+sales+company=base_pool`）由「先 quantize 兩個份額、company 吸收餘數」
  的順序保證，見 `calculation.py` 內註解。
- 自助續約業務分配為 0、其餘歸公司（不給現任維護人）：`compute_split()` 的
  `assisted=False` 分支。
- 退款反向原受款人/原政策、重送只調整一次：`create_commission_adjustment` 以
  `dedupe_key = hash(original_entry_id, refund_id)` 做內容去重（非僅 Idempotency-Key），
  並直接複製 `original.beneficiary_actor_id`，不轉嫁新接手人。
- 已付退款不自動扣薪：`recovery_method` 僅在原 entry 尚未進入已 accepted 的
  payout line 時才標 `ledger_offset`；已付款則標 `manual_review`，`payroll_offset_allowed`
  因此為 false（見 `CommissionAdjustment.to_api`）。
- 製作人不可自核：`approve_commission_run`／`approve_commission_payout_batch` 均比較
  `prepared_by == context.actor_id` 拒絕並回 `segregation_conflict`。
- 付款回執去重且部分成功不重複發放：`acknowledge_commission_payout_batch` 以
  `event_id` 記錄在 `batch.ack_event_ids`，重送直接回放現狀；`control_sha256` 不符即拒絕。

## 已知簡化與待核定項（NOT_CONNECTED / 待 steward 決定）

- **Adjustment 的 maker/checker**：OpenAPI 只提供一個
  `create_commission_adjustment` 命令，沒有獨立的覆核端點。本實作的 SoD 規則是：
  若 worker 消費 `finance.refund_confirmed` 事件時已建立
  `PendingAdjustmentDraft`（`prepared_by` 為服務 actor），之後人工呼叫
  `create_commission_adjustment` 即視為覆核（`approved_by` 為呼叫的 staff，與
  `prepared_by` 不同）；若沒有事件先行建立草稿（例如測試直接呼叫 API），則
  `prepared_by == approved_by == caller`，屬單一控制的人工路徑。這是暫定簡化，
  需 steward 決定是否要求第二位覆核者，已記錄於 `docs/18-decision-register.md`。
- **`year3_assisted_marketing_rule=approved_custom_rule`**：尚無核定的自訂尾佣表，
  `simulation` 會以 `zero_unless_continuous_auto` 語意回退並標 `unapproved_assumptions`；
  `live` 計算一律在 `unapproved_assumptions` 非空時仍會出帳（entries 會被標記
  `exception_count`），尚未阻擋 live——若 steward 要求更嚴格阻擋，需另行核定門檻。
- **`age_anchor`、`CommissionEntry.original_entry_id`**：`age_anchor` 目前只有
  `first_paid_effective_start` 一種已核定語意，由呼叫端（`OwnershipCohortPort` fixture）
  直接提供 `customer_year`，模組不再自行從交易時間反算；`original_entry_id` 於本實作
  恒為 null（commission.entry 為 append-only，沒有「entry 的 entry」鏈，保留欄位供未來
  擴充）。
- **`commission.calculation_run.mode`**：DB 欄位保留 `simulation`/`live`，但
  `preview_commissions` 完全不落地（`CommissionPreview` 是 ephemeral 回應，無
  `commission.calculation_run` 列），只有 `create_commission_run` 會寫入（永遠是
  `mode=live`）。
- 真實 Pantheon/Agora/LegendFlow 無關；本模組的外部邊界是 M10（conversion）、
  M11（ownership/cohort）、M14（revenue/refund）與財會薪酬介接，均為
  `NOT_CONNECTED`，只有本平台 adapter port 與 local fake（`ports.py`）。

## Admin UI 與資料庫提案

- `apps/admin/src/features/commissions/`：UI-24 `/commissions`（`CommissionWorkbench` 合併政策/試算/月結/調整/發放五個面板）與 UI-25 `/commissions/:id`（`EntriesView` 個人獎金明細）；所有畫面依 `allowed_actions` 顯示可操作按鈕，202 一律標示「受理非完成」。`acknowledge_commission_payout_batch` 是 payroll_adapter 的 machine-to-machine 回執，故意未提供人工按鈕。本機沙箱禁止 `npm install`（外部套件安裝），因此 `tsc`/`vite build` 為 **NOT_RUN**；程式碼已依既有 `strict`＋`noUncheckedIndexedAccess` tsconfig 手動核對型別窄化（特別是 `ViewState<T>` 判別聯集的窮舉收斂），交由 hosted CI 執行 `npm --prefix apps/admin run build` 驗證。
- `database/proposals/M12/`：baseline（`database/migrations/0001-0003`）缺少的欄位、兩個新增 append-only 子表，以及兩個與已實作流程衝突的既有 trigger 參數修正，逐項依據見該目錄 `contract-changes.md`。尚未套用到任何 PostgreSQL，標記 NOT_RUN；本模組持續使用上方「架構選擇」一節的 in-memory repository。

## 驗證指令

```bash
python3 -m pytest tests/modules/commissions -q
```

前端（NOT_RUN，見上）：

```bash
npm --prefix apps/admin run typecheck
npm --prefix apps/admin run build
```
