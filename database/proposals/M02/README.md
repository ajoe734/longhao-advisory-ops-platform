# M02 資料庫整合備註

基準：`48ae302308de64159ee25ed506fb6f2e548cf886`。本目錄不是已套用 migration；integration/data steward 應核對下列對映後在 disposable PostgreSQL 16 實跑。

## 現況

`database/migrations/0001_domains.sql` 的 `contract` schema 已含本模組全部八張權威表（`package_version`、`customer_signature`、`acceptance_decision`、`company_acceptance`、`contract`、`contract_service`、`entitlement`、`entitlement_decision`），且 `0002_isolation_indexes.sql`／`0003_business_guards.sql`／`0004_database_roles.sql` 已覆蓋 tenant RLS、版本保護、maker-checker trigger（`contract.guard_company_acceptance`）、生效 gate trigger（`contract.guard_contract_effective`）、active entitlement 付款條件 trigger（`contract.guard_active_entitlement`）及 `longhao_m02` 角色授權（含對 `customer.application`、`customer.customer`、`kyc.submission`、`kyc.suitability_assessment`、`aml.business_clearance`、`finance.payment` 的 SELECT）。本模組的參考實作（`apps/api/src/modules/contracts_entitlements/`）未發現基線無法表達的欄位缺口，因此**本次不提出新增 migration**。

## 實作與資料表的對映備註

| 參考實作概念 | 資料層 | 說明 |
| --- | --- | --- |
| `PackageVersion.version_label` | 不在 `contract.package_version` 逐欄清單 | 參考實作另存人類可讀標籤；`package_version`（int）仍是 DB 唯一序號。production repository 需在同一列同時保存兩者，或由 API steward 確認是否要正式新增欄位。 |
| `SignatureSession`（pending/verified/failed/expired） | 無對應資料表 | 對齊 `report.signature_request` 的設計慣例（pending/submitted/failed/expired 存請求表，驗證成功才落地 `contract.customer_signature`）；本模組的 session 屬同類「請求態」物件，可仿 `report.signature_request` 新增 `contract.customer_signature_request`，由 integration steward 評估是否併入正式 migration。 |
| `EntitlementDecision`（API，批次彙總） | `contract.entitlement_decision`（每 entitlement 一列） | 參考實作為降低樣板，將同一次 `evaluate_entitlements` 呼叫的多個 entitlement 彙總成一個回應物件；repository/read-model adapter 落地時仍須展開為多列，`entitlement_id` 欄位維持單數、每列一個 FK。 |
| `AmendmentRecord`（API 回應套用 `Application` schema） | 無獨立資料表；寫入新的 `contract.contract_service`／`contract.entitlement`（`predecessor_service_id`／`service_chain_id` 鏈接） | 本模組不擁有 `customer.application`；回應借用既有 `Application` schema 純為轉達「待生效續約」的狀態投影，不代表 M02 寫入了 `customer.application`。 |

## 待 API/事件 steward 確認

- `EntitlementDecision.entitlement_ids`（複數）與 DB `entitlement_decision.entitlement_id`（單數）的彙總關係是否要在 OpenAPI 明確文件化。
- `create_contract_amendment` 是否應改用專屬回應 schema，而非借用 `Application`。

以上均為契約協調項，不在本次私自變更 `api/openapi.yaml`／`events/registry.json`。
