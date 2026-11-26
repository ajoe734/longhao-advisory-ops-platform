# M04 共用契約相容新增提案

此檔不直接修改 `api/openapi.yaml` 或 `events/registry.json`，由 integration steward 合併：

1. **`risk_level` 列舉不一致**：OpenAPI `AmlCase.risk_level` 與 `AmlDecisionRequest.risk_level` 是 `low/standard/high`；`database/data-dictionary.md` 與 `catalog.json` 的 `aml.case_file` CHECK 是 `low/medium/high/undetermined`。本提案依 OpenAPI（wire contract 權威）修正 DB CHECK 為 `low/standard/high`（見 `001_aml_cft.sql`），建議資料字典與 catalog.json 同步更新，不在模組內另造列舉或 mapper 掩蓋落差。
2. **`AmlCase.screening_ids` / `restricted_evidence_ids` 為陣列**，但基線 `aml.case_file` 只有單一 `screening_id` FK，且沒有案件層級的受限證據集合。本提案新增 `aml.case_screening_link`、`aml.case_evidence_link` 兩張 append-only 關聯表，`case_file.screening_id` 保留作為觸發案件的首個 screening（向下相容），完整集合由新表投影。
3. **`submit_aml_declaration` 無對應資料表**：`database/catalog.json` 未列出任何儲存 `AmlDeclarationReceipt` 的表。本提案新增 `aml.declaration_receipt`（一個 application 一筆，可補件更新版本），並明確不作為 `aml.business_clearance` 的輸入來源（宣告本身不是查核，呼應 UAT-M04-007）。
4. **內稽限定存取缺公開 operationId／event**：REQ-M04-010 要求「限定案件、有效期內」的內稽讀取；目前 OpenAPI 的 `get_aml_case` 只開放 `aml_officer`/`aml_supervisor`。本提案新增 `aml.case_read_grant` 資料表供應用層核發／核對時效 grant；建議新增 `grant_aml_case_read` operationId（角色 `aml_supervisor`，需 reason_code 與 expires_at）與對應審計欄位，由 API steward 排入下一輪契約。在契約合併前，模組只在內部 application service／測試使用 `grant_case_read`，不掛公開 production route。
5. **保全（legal hold）沿用既有 `core.legal_hold`**：不新增表；`apply_legal_hold` 以 `target_type='aml_case_file'`、`target_id=case_id` 寫入既有共用表，解除同樣以新 `action='release'` 列表達（見總體資料字典第 7 節）。本次模組內部能力 `apply_legal_hold` 尚未掛公開路由，待 API steward 確認是否納入共用保全 API 或維持模組內部能力。
6. **`decide_aml_case` 的核定證據／原因屬於案件軌跡**：正式資料層建議在核定時同時於 `aml.case_action` 插入一筆 `action_code IN ('decision_approve','decision_deny','decision_request_information')` 的紀錄並透過 `aml.case_evidence_link` 連結其證據，不再新增專屬表。
7. **`aml.screening` 需要 `scope`／`reason_code`／`manual_required`**：OpenAPI `ScreeningRequest.scope`、`reason_code` 與 UAT-M04-004 的「manual_required」在基線缺對應欄位；本提案以 `ADD COLUMN IF NOT EXISTS` 補上（見 `001_aml_cft.sql`）。

在上述契約合併前，模組內對應能力只在測試／受控 application service 使用，不自造隱藏 topic 或私有 production 端點；真實 LegendFlow／名單 provider 接線仍是 `NOT_CONNECTED`。
