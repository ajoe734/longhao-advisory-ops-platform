# M10 共用契約相容新增提案

本模組實作完全落在既有 `api/openapi.yaml`（10 個 M10 operationId）與
`events/registry.json`（M10 的 20 個訂閱事件＋3 個發出事件）範圍內，**未**
提出新的 OpenAPI operation 或事件註冊；只新增 DB 層的 `crm.opportunity`／
`crm.work_item`／`crm.conversion`／`crm.suppression`／`crm.activity` 欄位與
CHECK 擴充（見 `001_crm.sql`）。

## API ↔ 資料表 mapper（本模組新增，供審閱）

| API 欄位 | 資料層 | 說明 |
| --- | --- | --- |
| `Opportunity.type` | `crm.opportunity.kind` | `new_subscription↔new`、`retention↔retention`（既有 CHECK 新增）、其餘同名 |
| `Opportunity.state` | `crm.opportunity.status`（投影） | `qualified↔qualifying`、`proposal↔proposed`、`suppressed↔cancelled`；本模組只在判定需重評/抑制時寫入 `cancelled`，無其他 client 可達路徑寫入該值，故 DB→API 方向無歧義 |
| `Opportunity.contract_id` | `crm.opportunity.contract_service_id` | 沿用既有欄位；見下方「contract_id 的兩種粒度」 |
| `CrmWorkItem.worklist`／`reason_codes`／`evidence_refs`／`suggestion_origin` | `crm.work_item` 新增同名欄位 | 取代單一 `reason_code`／不可結構化的 `explanation` jsonb |
| `CrmWorkItem.state` | `crm.work_item.status`（投影） | `assigned`/`snoozed`→API `open`；有效 suppression 時強制投影為 `suppressed`（即使底層仍是 open/assigned/snoozed） |
| `Conversion.conversion_type` | `crm.conversion.conversion_kind`（新欄位） | 與既有 `conversion_type`（self_service/sales_assisted）是不同軸，見下方 |
| `Conversion.state` | `(crm.conversion.decision_status, len(claim_history))`（投影） | `decision_status='proposed'` 且 `len(claim_history)<=1`→`claimed`；`>1`→`under_review`；其餘同名 |
| `Conversion.claimed_salesperson_id`／`approved_salesperson_id` | `crm.conversion.claimed_salesperson_id`（新）／`sales_actor_id`（既有） | 分開保存，核定可與最初認領人不同（REQ-M10-008） |

## `contract_id` 的兩種粒度（已知假設，待確認）

`crm.opportunity`/`crm.conversion` 的既有 FK 指向
`contract.contract_service`（服務線層級），本模組 API 的 `contract_id`
欄位（`Opportunity`/`ConversionClaim`/`Conversion`）沿用這個服務線層級語意。
但 `contract.terminated`/`contract.effective`/`finance.refund_confirmed`
等已發布事件的 `contract_id` 欄位是 M02 的**上層** contract 概念，兩者粒度
不同。`finance.refund_confirmed` 的回正邏輯
（`CrmService.reverse_conversion_for_refund`）用 `contract.service_ended`
事件學到的 `contract_service_id → contract_id` 對應表解析；在該對應尚未
出現前，暫以「refund 事件的 contract_id 直接等於
contract_service_id」為後備假設（單一服務線的合約必然成立）。這是本模組
自行採用的假設，**非已核准契約**；若多服務線合約常見，應請契約治理者在
`finance.refund_confirmed`／`contract.terminated` 的事件schema 加上
`contract_service_id`（additive 欄位），而非由本模組永久依賴此假設。

## `conversion_type` 兩軸並存（已知相容性風險）

既有 `crm.conversion.conversion_type` CHECK 僅接受
`self_service`/`sales_assisted`，語意是「歸因結果」。本模組 API 另需
`new_subscription`/`renewal`/`upgrade`/`reactivation`（機會/轉換種類），
兩者是正交維度，本提案新增 `conversion_kind` 欄位承載後者，既有
`conversion_type` 欄位維持原意、由 `decide_conversion` 的
`approved_salesperson_id` 是否為 null 推導（null→self_service，
非null→sales_assisted），不是使用者直接輸入的欄位。若日後資料治理者認為
`conversion_type` 這個既有欄位名稱容易與 API 的 `Conversion.conversion_type`
混淆，建議之後的契約變更把其中一邊改名；本提案不擅自重新命名既有欄位。

## 與其他模組提案的相依關係

- `database/proposals/M01/contract-changes.md` 等其他模組提案若新增/變更
  事件註冊，本模組目前只依賴 `events/registry.json` 已登錄的 20 個訂閱
  事件；尚未核准的對方模組契約變更不在本模組假設範圍內。
- `crm.conversion`/`crm.opportunity`/`crm.work_item`/`crm.suppression`/
  `crm.activity` 僅本模組擁有寫入權；未更動其他模組擁有的資料表或唯讀
  port 契約。

## 待議：`core.object_asset` 存在性檢查介面（未實作，僅記錄缺口）

本模組 `record_sales_activity`／`decide_conversion`／
`create_crm_suppression` 均需驗證引用的 evidence/reason_ref 資產存在；
目前以 `apps/api/src/modules/crm/ports.py` 的 `EvidencePort`／
`FakeEvidencePort` 模擬，真實 `core.object_asset` 唯讀查詢介面由哪個模組
提供、如何跨模組暴露，尚待 integration steward 核定後再接線；
production 環境下 `CrmService`／`CrmAutomationService` 建構子已 fail-closed
（偵測到 `is_fake` adapter 直接 raise），不會悄悄用假證據通過驗證。
