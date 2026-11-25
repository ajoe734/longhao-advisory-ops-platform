# M08 建議序列、簽章驅動發布與更正模組

此目錄實作 OpenAPI 1.0.0 的 6 個 M08 operationId。`domain.py`／`service.py` 保存業務不變量，`http.py` 提供
framework-neutral controller 及可由 shared app 掛載的 FastAPI router；`ports.py` 的 fake 只接受合成
fixture，production composition 會拒絕 fake adapter。

關鍵控制：`report.signature_verified` 是唯一發布觸發（無第二個人工 Publish 步驟）；內容在
`create_recommendation_version` 寫入一次後不可變，簽後偽換文字由 `ReportBundlePort` 的 bundle hash
cross-check 擋下（REQ-M08-005）；受眾在發布當下 seal 成 snapshot，之後的權益撤銷（`entitlement.changed`）
只更新即時索引，不回寫 snapshot/recipient 歷史（REQ-M08-006/007）；`report.signature_verified` 重送由
inbox payload-hash 去重，同一事件只發布一次（REQ-M08-008）；撤回（`withdraw_publication`）與更正
（`link_publication_correction`）都只新增事件與狀態轉移，不覆寫已發布證據（REQ-M08-009/010）。

`RecommendationSeries.state`／`RecommendationVersion.state` 在資料字典中沒有對應欄位，本模組依
`publication.publication` 的存在與時間純推導（`_series_derived_state`／`_version_derived_state`），不另
造隱藏狀態欄位。

簽章後的實際發布由 `PublicationService.process_publish_job` 執行（供
`workers/tasks/publication/tasks.py::PublicationWorker` 呼叫），具 lease、bounded retry、timeout 與
deadletter；過期訊息一律 blocked，不會被重送當作新建議（REQ-M08-011）。`source_candidate_id` 與
`valid_from/valid_until` 讓建議時間與來源 Persona 交易時間分列（REQ-M08-012）。

外部狀態：M07 簽章 bundle、M02 權益、M09 通路均為 `NOT_CONNECTED`；本模組只有 adapter port
（`ContentAssetPort`／`ReportBundlePort`／`EntitlementAudiencePort`／`EvidencePort`）與 local fake，沒有
修改或呼叫外部 repository／production endpoint。

已知契約缺口（見 `database/proposals/M08/contract-changes.md` 第1節）：OpenAPI
`RecommendationVersionCreate.supporting_report_version_ids` 允許空陣列，但
`RecommendationVersion` 回應要求至少 1 項；本模組在業務層要求至少 1 項（比 wire 契約更嚴格但不違反
它），任務包描述的「draft 保留空陣列」流程尚未有對應的可回傳 operationId，待 API/Event steward 核定。

整合者需提供：shared PostgreSQL repository/UoW、FastAPI auth context resolver、真實 M07 bundle
cross-check 端點、M02 entitlement 查詢端點、metrics exporter，以及套用 `database/proposals/M08` 後的
migration 編號。
