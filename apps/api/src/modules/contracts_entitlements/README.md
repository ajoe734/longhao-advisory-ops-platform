# M02 契約與權限模組

此目錄實作 OpenAPI 1.0.0 的 12 個 M02 operationId。`domain.py`／`service.py` 保存業務不變量，`http.py` 提供 framework-neutral controller 及可由 shared app 掛載的 FastAPI router；`ports.py` 的 fake 只接受合成 fixture，production composition 會拒絕 fake adapter（建構子在 `environment="production"` 時檢查 `is_fake`）。

## 架構決策

- 沿用 M01 的獨立模組模式：本模組擁有自己的 `InMemoryRepository`／`StoreState`（`repository.py`），而非直接掛上 `longhao_platform.store.InMemoryAtomicStore`。`app.py`／`bus.py` 是共用 composition root 的參考行為；`plugin.py` 以本模組的 `InMemoryRepository` 與 fake ports 註冊 router（僅限非 production）；production PostgreSQL repository 與跨模組 port 由 integration steward 在契約凍結後補上。
- Gate 狀態（KYC、適合度、AML clearance、付款）一律透過 `ApplicationSourcePort`／`KycPort`／`SuitabilityPort`／`ClearancePort`／`PaymentPort` 即時查詢，對應 `database/migrations/0004_database_roles.sql` 已授予 `longhao_m02` 的 SELECT 權限（`customer.application`、`customer.customer`、`kyc.submission`、`kyc.suitability_assessment`、`aml.business_clearance`、`finance.payment`）。事件只觸發重查（見 `workers/tasks/contracts_entitlements/consumer.py`），不被當成已獲授權的證據。
- `contract.contract` 只在 `decide_company_acceptance(decision=accept)` 成功時建立（而非簽署時）；`contract.customer_signature`／`SignatureSession` 在簽署階段先行存在。這與 `database/catalog.json` 的 `contract.contract` CHECK（`status IN ('accepted','effective')` 要求 `company_acceptance_id IS NOT NULL`）一致。
- `evaluate_entitlements` 的回應是「本次評估批次」的單一 `EntitlementDecision` 投影，彙總該次所有受影響的 entitlement；production schema（`contract.entitlement_decision`）每個 entitlement 各一列，由 repository/read-model adapter 在落地時展開，詳見 `database/README.md` 的 API/資料表 mapper 慣例。
- `create_contract_amendment` 的回應借用 OpenAPI 既有 `Application` schema 表示「待生效續約/升級」投影；M02 並不擁有 `customer.application`，真正需要重新簽署或公司覆核的升級仍須回到 M01 的 `create_application`。本模組只處理「沿用既有公司接受、僅延展同一契約下服務期間」的情境（見 `AmendmentRecord`）。

## 關鍵控制

可信任 context 決定 tenant/actor；CAS（`If-Match`）、payload-bound idempotency（外部 side effect，例如 `signature_provider.create_session`，一律先查有無既存 receipt 再呼叫，避免 replay 產生第二個 provider session）、maker-checker（package 提案者不可自行核准；package 製作者/核准者不可同時擔任同一申辦的公司接受者 → `segregation_conflict`）、stale-assessment 檢查（公司接受引用的 KYC/適合度/AML 必須是當前版本，否則 412 `stale_assessment`；且 signature 與引用的 package_version_id 須一致、package 須為 `approved`、同一 application 不可重複建立 contract）、審計與 outbox 原子提交、entitlement 狀態機（`locked → eligible → active → suspended/expired/revoked`）皆在同一交易內完成。`evaluate_entitlements` 每次重算會即時重讀 KYC/適合度/AML port（不只看 payment condition），任一失效即把目標狀態降為 `suspended`；`entitlement.changed`／`terminate_contract` 撤銷事件的 `decision_id` 一律對應真正落地的 `contract.entitlement_decision.id`。`receive_customer_signature` 只接受 `signature_adapter` 服務身分，且一律以 webhook 金鑰解析出的 `tenant_id` 比對 session 所屬租戶，不信任 callback body 自帶的任何租戶線索。

## 外部狀態（NOT_CONNECTED）

LegendFlow 簽章 provider、真實 KYC/適合度/AML/金流來源、LegendFlow entitlement projection 推送、webhook HMAC 驗簽金鑰，均為本次 `NOT_CONNECTED`；僅建立本平台 adapter port 與 local fake（`ports.py`）。`WebhookVerifierPort.resolve_tenant(key_id)` 模擬「每租戶各自簽章金鑰」的 production 慣例；真實 key provisioning 待簽章 provider 契約生效後由 integration steward 接上。

## 整合者需提供

Shared PostgreSQL repository/UoW、FastAPI auth context resolver、webhook HMAC 驗簽金鑰管理、`ApplicationSourcePort`／`KycPort`／`SuitabilityPort`／`ClearancePort`／`PaymentPort` 的真實 adapter（對應上列既有 SELECT 授權）、metrics exporter，以及套用 `database/proposals/M02` 備註後的生產 migration 編號。共用 OpenAPI 的 `EntitlementDecision`／`entitlement_id` 單複數差異、`create_contract_amendment` 回應是否應改用專屬 schema，均留待 API steward 確認；本模組不私自變更 wire contract。
