# M04 AML/CFT 查核、風險與受限案件模組

此目錄實作 OpenAPI 1.0.0 的 8 個 M04 operationId。`domain.py`／`service.py` 保存業務不變量，`http.py` 提供 framework-neutral controller 及可掛載的 FastAPI router；`ports.py` 的 fake 只接受合成 fixture，production composition 會拒絕 fake adapter。

## 關鍵控制

- 可信任 context 決定 tenant/actor/scope；一般 sales/service 角色沒有 `aml_officer`/`aml_supervisor`/`company_acceptor`，讀 AML 案情一律 403。
- 命中解除與關係核定只接受 `principal_type == "staff"` 且角色正確；任何 `service`（包含 LLM 摘要 service principal）呼叫 `decide_aml_case` 一律 403，不論其 scope 為何（見 `automation.py` 的隔離設計）。
- `add_aml_case_action` 與 `decide_aml_case` 皆採 maker-checker：對同一案件新增過 action 的 actor，不能再核定該案件（`maker_checker_violation`）。
- 名單 provider 失聯／結果未知保持 `pending`/`manual_required`，絕不等同 `clear`；`start_aml_screening` 只建立 `pending` screening 並排入 dispatch job，真正對外呼叫在 worker（`workers/tasks/aml_cft`）內完成，不在 DB 交易中等待網路。
- `aml.business_clearance` 依資料字典標記為 append-only：每次決策（screening clear/error、case 決定、EDD 要求）都建立新版本列，舊版本保留供追溯；`get_business_clearance` 只回最新版本，並在讀取時把已過期的 `approved` 投影為 `expired`（不回寫 storage）。
- 受限內稽存取採獨立 grant（`grant_case_read`，尚未公開路由，見下）：`internal_auditor` 角色沒有案件瀏覽權，只能用授權人核發、限定案件、有效期內的 grant 讀取單一案件；每次讀取皆落 audit。

## 本模組對 OpenAPI／資料字典落差的實作決定

- OpenAPI `AmlCase.risk_level`／`AmlDecisionRequest.risk_level` 列舉為 `low/standard/high`；`database/data-dictionary.md` 與 `catalog.json` 的 CHECK 是 `low/medium/high/undetermined`。OpenAPI 是 wire contract 權威，本模組一律使用 `low/standard/high`（新案件預設 `standard`）；`database/proposals/M04/contract-changes.md` 已列為契約協調項，建議資料字典改用相同列舉，而不是本模組私自新增列舉。
- `submit_aml_declaration` 的 If-Match 比對對象是本模組自建、以 `(tenant_id, application_id)` 為鍵的 `AmlDeclaration` 紀錄版本（OpenAPI 未另外定義宣告聚合）；首次提交沿用 M01 `create_*` 慣例不需 If-Match，之後補件才需版本比對。`decide_aml_case`／`add_aml_case_action` 的 If-Match 比對對象是路徑上的 `aml_case_id` 本身，與 OpenAPI 描述一致。
- `database/catalog.json` 未列出一張對應 `AmlDeclarationReceipt` 的資料表；`database/proposals/M04/001_aml_cft.sql` 新增 `aml.declaration_receipt` 作為契約所需的持久化證據，並在 `contract-changes.md` 說明。
- 內稽 grant（`grant_case_read`）與保全（`apply_legal_hold`）都是模組內部能力，尚未有對應公開 operationId／event；對齊 M01 `revoke_external_identity` 的作法，只在受控 application service／測試使用，不掛公開 production route，也不自造 topic。

## 外部狀態

LegendFlow／Pantheon／真實名單 provider（sanctions/PEP/adverse media）均為 `NOT_CONNECTED`。本模組只有 `ScreeningProviderPort` 與本地 fake；沒有修改或呼叫外部 production endpoint。M02 的 `application_id -> customer_id` 投影透過 `ApplicationAccessPort` 注入，真實串接由 M02 adapter 提供。
