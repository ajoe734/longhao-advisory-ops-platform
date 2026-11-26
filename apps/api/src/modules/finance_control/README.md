# M14 帳務、對帳、退款與內控證據模組

此目錄實作 OpenAPI 1.0.0 的 24 個 M14 operationId（`docs/modules/M14-finance_control.md` 第 8 節）。`domain.py`/`service.py` 保存業務不變量，`http.py` 提供 framework-neutral controller 及可由 shared app 掛載的 FastAPI router；`ports.py` 的 fake 只接受合成 fixture，production composition 會拒絕 fake adapter（`environment == "production"` 時拒絕任何 `is_fake` adapter）。

## 關鍵不變量（對應 UAT-M14-001~012）

- 付款／退款 webhook 以 `event_id` 做業務層去重（`receive_payment_webhook`、`receive_refund_webhook`）；同 id 同 payload 回 `duplicate`，不重複入帳；同 id 異 payload 回 `quarantined` 而非盲目覆寫。
- `finance.invoice` 的 `untaxed_service_amount`/`tax_amount` 由呼叫端明確提供（不猜除數）；`tax_mapping_version` 未核准時 `create_invoice` 直接以 409 `tax_mapping_not_approved` 拒絕，核准後可重送。
- 付款入帳只增加 `Invoice.paid_amount`（預收），`finance.service_schedule`／`finance.revenue_recognition` 只在 `run_revenue_recognition` → `approve_revenue_run` 之後才真正確認收入與計獎基礎；`dry_run=true` 的批次永遠不能被核准過帳。
- 退款 maker/checker/executor 三權分立：`request_refund`（finance_operator/service_agent）、`approve_refund`（finance_checker，且 approver≠preparer）、`execute_refund`（refund_service 服務身分，且 executor≠approver）。
- `execute_refund` 一律先呼叫 `RefundProviderPort.query()`；只有查無記錄才 `submit()`，故同一 `provider_idempotency_key` 永遠只會對外送出一次退款。
- 退款 allocation 明確區分 `unearned_amount`（預收沖還）與 `earned_reversal_amount`（已認列收入沖回，必須引用既有 `commission_eligible` 的 `recognition_id`）；日記帳分開過帳，只有 earned reversal 會在未來交由 M12 事件消費方做計獎反向。
- `_post_journal` 在寫入前檢查借貸合計相等，不平衡直接 409 `journal_unbalanced`，不會產生「已過帳」標記；`create_accounting_export` 對每筆引用的 journal 也重新核對平衡與 `posted` 狀態。
- `acknowledge_accounting_export` 以 `event_id` 去重；重送只回放既有 export 狀態，不二次過帳；`export_sha256` 不符直接 409。
- `core.legal_hold` 以兩個事實（hold/release）表達，從不刪除或覆寫原 hold；`release_legal_hold` 要求操作者不等於建立者（SoD），`evaluate_purge_candidate()` 在有效 hold 時回報 `skip` 並留 `reason_code`。

## 外部狀態（NOT_CONNECTED）

- 真實金流／退款 provider、銀行對帳檔解析、會計 ERP 接口均為 `NOT_CONNECTED`；本模組只提供 `RefundProviderPort`/`SettlementStatementPort` 與 local fake，沒有呼叫或修改任何外部系統。
- `create_payment_intent` 在沒有 `invoice_id` 時，`customer_id` 由 `application_id` 以 deterministic uuid5 占位推導（`_placeholder_customer_id`），待 M01/M02 的 application→customer 解析 adapter 到位後需改為權威查詢，目前以 `NOT_CONNECTED` 標記。
- `finance.payment_intent` 不是資料字典中的獨立表；本實作把 intent 階段直接映射為 `finance.payment` 的 `pending` 狀態列，`checkout_url`/`expires_at`/`method`/`return_route` 屬本模組新增的操作型欄位（非資料字典列舉值變更），詳見 `database/proposals/M14/contract-changes.md`。
- `Refund.state == "closed"` 目前沒有對應的公開 API 可達；24 個 operationId 中沒有 `close_refund`，`accounting_adjusted` 是本模組目前的終態，`closed` 保留給未來的歸檔批次工作（如實作則需契約變更）。
- `run_revenue_recognition` 的例外偵測目前只覆蓋「契約已終止但服務期跨過終止日」一種情境；settlement 對帳只偵測 `unmatched_settlement`/`duplicate`/`amount_mismatch`，`fee_mismatch`/`timing` 尚未有可靠輸入來源，保留於 `automation.py` 的提示詞彙但判定邏輯待後續資料來源到位。

## LLM/自動化邊界

`automation.py` 的 `ReconciliationNarrationService` 只接受 minimized 對帳欄位（UUID/金額/幣別/差異類型），保存 model/prompt/ruleset/source 版本並產生 `untrusted_draft`；它沒有解決、核准或過帳的命令權，只能被 finance_operator/finance_checker 呼叫取得草稿說明。沒有核准的真實 LLM provider 時使用 deterministic local fallback，production 會拒絕 fake。

## 整合者需提供

shared PostgreSQL repository/UoW、FastAPI auth context resolver、真實 payment/refund provider adapter、銀行結算檔解析服務、會計 ERP adapter、object evidence adapter、metrics exporter，以及套用 `database/proposals/M14` 後的 migration 編號。
