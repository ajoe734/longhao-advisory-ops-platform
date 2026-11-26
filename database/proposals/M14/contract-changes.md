# M14 共用契約相容性說明

此檔不直接修改 `api/openapi.yaml` 或 `events/registry.json`；記錄本次實作遇到的 wire／DB 對映落差，供 integration/API/data steward 核對是否需要正式契約變更。

1. `finance.payment` 沒有獨立的 `payment_intent` 表；`PaymentIntentCreate`/`PaymentIntent` 直接投影到 `finance.payment` 的 `pending` 狀態列（見 `001_finance_control.sql` 新增 `method`/`return_route`/`checkout_url`/`expires_at`）。這與資料字典第 7 節「本模組權威寫入」表列一致，不新增表。
2. `Invoice.state` 的 `partially_paid` 是 API 投影，不是 `finance.invoice.status` 的 DB CHECK 值（仍是 `draft/issued/paid/voided/credited`）；由 `paid_amount` 與 `total_amount` 比較在 repository/read-model 算出，符合模組文件第 6 節「不能直接當資料庫status字串」的要求。
3. `finance.reconciliation_item` 的既有 `source_type`/`source_ref`/`difference_amount` 與新補的 `settlement_id`/`difference_type`/`expected_amount`/`actual_amount` 並存一段時間；mapper 寫入時雙寫，待下游讀者全部切到新欄位後，由後續 migration 棄用舊欄位。
4. `finance.refund_allocation` 仍以 `allocation_type IN ('earned_revenue','unearned_prepayment','tax','fee')` 一列一類別儲存；wire 的 `RefundAllocation` 把同一 `invoice_line_ref` 下最多三個金額（`unearned_amount`/`earned_reversal_amount`/`tax_adjustment_amount`）表示成一個物件。Mapper 在 `request_refund` 寫入時，依非零金額各寫一列並帶共同 `invoice_line_ref`；讀出時依 `invoice_line_ref` 分組還原。
5. `finance.accounting_export.status` 的 DB 既有列舉（`prepared/submitted/acknowledged/rejected/reconciled`）與 OpenAPI `AccountingExport.state`（`prepared/approved/exported/partially_acknowledged/acknowledged/failed`）不是一對一；本提案把 CHECK 擴成兩邊列舉的聯集，由應用層只寫 wire 需要的值，`submitted`/`reconciled` 保留給既有舊資料相容，新資料不再寫入。
6. `finance.control_exception.status` 同理擴成聯集（新增 `ready_for_review`/`reopened`）；舊列舉 `assigned`/`awaiting_review`/`waived` 保留供其他既有呼叫端相容，M14 本次程式碼只使用 wire 列舉。`resource_type`/`resource_id` 兩欄不在 wire `ControlException` schema 中；本模組寫入時填 `('control_exception', id)` 自我參照，待未來若有多模組共用的控制項資源再改回真實指向。
7. `finance.revenue_run` 新增 `calculation_detail jsonb` 只是審計用的計算輸入快照，不是新的對外欄位；`RevenueRun` wire schema 不變。
8. 新增的 `finance.reconciliation_automation_run`/`_review` 不在 OpenAPI 中公開；它們支援模組文件第 11 節「LLM 可整理對帳差異說明」的草稿留痕，沒有 API operationId，也沒有解決對帳項目的權限。若未來要把草稿透過 API 曝光，需另外走正式契約變更，不能先開 route 再補 schema。
9. `execute_refund` 的 `provider_idempotency_key` 與建立退款時的 `Idempotency-Key` header 是兩個獨立的去重鍵；前者對外（provider），後者對內（命令重送）。這不是 schema 缺陷，只是提醒讀者不要混用。

在上述列舉聯集與雙欄並存被下游讀者全部確認切換前，`finance.*` 的其他模組只應透過本模組發出的事件（`finance.payment_confirmed`／`finance.revenue_recognized`／`finance.refund_confirmed`／`finance.accounting_export_acknowledged`）或公開 API 讀取，不應直接查詢上述過渡期雙欄位。
