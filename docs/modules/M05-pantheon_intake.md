# M05 · Pantheon 候選研究接收與證據驗證：SA / SD 單元規格

版本：1.0 · 文件狀態：可平行實作的契約基線 · 實作／商務UAT狀態：尚未宣稱完成。

## 1. 目的與交付範圍

接收 Persona 自主研究／交易 pool 形成的候選研究，也支援分析師發起的研究。保存來源、時點、版本、證據與可用權利，轉交專業覆核；本模組不管理 Persona 的交易決策。

本模組與其餘十三個模組同時排程，涵蓋下列全部需求；跨模組整合依賴以凍結API、事件及mock先解耦，不以其他模組尚未完成作為不開工理由。正式整體交付仍須十四模組和跨域旅程全部驗收。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 2. 使用者、責任與授權

- Pantheon adapter principal：僅可送入指定 tenant/source 的候選與 lineage
- 研究營運：處理不完整、重送、未知 schema 與失敗收件
- 分析師：讀取可用候選及原始來源，不得改原始 received payload
- 平台維運：處理 adapter 健康與 DLQ，不取得客戶個資或簽章權限

角色名是業務責任；實際API scope/role見第8節與OpenAPI。每個request由可信任server決定tenant、actor與scope；瀏覽器傳來的customer_id不能自行授權。平台admin不因此取得全部業務資料。

## 3. 必備功能與驗收需求

| 需求ID | 必備能力／驗收情境 | 通過條件 | UAT |
| --- | --- | --- | --- |
| REQ-M05-001 | Persona 可自主建立候選 | 建立immutable candidate與review待辦；不要求先由分析師建研究 | UAT-M05-001 |
| REQ-M05-002 | 分析師發起成果走同一通道 | 同樣驗證與建review；不跳過證據、覆核或簽章 | UAT-M05-002 |
| REQ-M05-003 | 候選重送只建一覆核案件 | 一次inbox applied，第二次duplicate；同candidate/review id | UAT-M05-003 |
| REQ-M05-004 | 相同版本不同hash進隔離 | 衝突／quarantine；A不被覆寫；不能進簽章佇列 | UAT-M05-004 |
| REQ-M05-005 | 缺少必要證據不可ready | 指出缺失並保持quarantined；有raw來源receipt供追查 | UAT-M05-005 |
| REQ-M05-006 | 外部策略approved不當正式投顧核准 | 只能進review；member feed不存在該候選 | UAT-M05-006 |
| REQ-M05-007 | 研究時間不得倒填交易時間 | 保存trade/source/report/received各真實時間，不把report time設為trade time | UAT-M05-007 |
| REQ-M05-008 | 未知schema版本明確拒絕 | 422 unsupported_schema，保存安全receipt；不默認忽略新必要欄位 | UAT-M05-008 |
| REQ-M05-009 | 外部證據URL阻擋SSRF | 拒絕allowlist之外的地址與重新導向；不對任意URL發請求 | UAT-M05-009 |
| REQ-M05-010 | 授權不足的來源不可客戶發布 | 標明customer_distribution不允許；需替換或取得權利，不自動公開全文 | UAT-M05-010 |
| REQ-M05-011 | 新候選版本通知舊review過時 | v1保留；M06收到具版本事件並標示stale/rebase，不自動簽v2 | UAT-M05-011 |
| REQ-M05-012 | Pantheon故障不丟棄候選 | 可在failure queue查與重送；一次成功後不重建case，原收到時間不變 | UAT-M05-012 |

## 4. 主要使用案例

### UC-M05-A · Persona 自主候選

Pantheon 發出 candidate envelope，包含 persona/pool/research/artifact/evidence references及 occurred_at/available_at；本地 inbox 驗證、去重，取得允許的 evidence snapshot，建立 review case。沒有分析師預先建立 ticket也能成立。

### UC-M05-B · 分析師發起候選

人工在外部研究系統發起的成果走同一接收通道；origin_type區分，不建立另一套審核／發布邏輯。

### UC-M05-C · 資料缺失與修正

缺證據、授權不明、時間矛盾或hash不符時隔離並請求外部補正；新候選保留parent與supersedes鏈，使M06知道目前覆核是否過時。

## 5. 流程階段與拒絕條件

下表說明業務流程階段；例如「已驗證簽章」「已送達」「unknown退款」可能由多個資料列／欄位投影，不能直接當作資料庫status字串。實際寫入列舉在第6節。

| 流程 | 階段／允許方向 | 前置條件與不可跨越的邊界 |
| --- | --- | --- |
| candidate | received → validating → ready/quarantined/rejected | ready 表示足夠進覆核，絕非 approved report；原始外部 id/version/hash 唯一 |
| source fetch | pending → fetched → hash_verified/failed/denied | 只抓allowlisted服務與物件，簽名URL檢查有效期與範圍 |
| revision lineage | parent → immutable successor | 新版本建新紀錄；同id/version不同hash是衝突，不悄悄替換 |

所有不可逆或有財務影響的命令先驗actor/SoD、再比版本、再驗業務前提；成功時狀態、audit與outbox在同一交易提交。外部呼叫在提交之後由worker處理，不把開著的DB transaction跨網路等待。

## 6. 實際儲存狀態與模型對映

| 資料表 | 狀態欄位 | 語意 | SQL列舉／限制 |
| --- | --- | --- | --- |
| research.candidate | status | 狀態：received / validating / quarantined / ready / rejected / superseded | CHECK (status IN ('received','validating','quarantined','ready','rejected','superseded')) |
| core.inbox | status | 狀態：received / leased / applied / rejected / retry / dead_letter | CHECK (status IN ('received','leased','applied','rejected','retry','dead_letter')) |
| core.outbox | status | 狀態：pending / leased / published / retry / dead_letter | CHECK (status IN ('pending','leased','published','retry','dead_letter')) |

`report.signature`只存驗證成功證據；pending/submitted/failed/expired在signature_request，驗簽invalid結果映射failed及error_code，撤銷在revocation。`delivery.message.status`不儲存read；已讀由有證據的receipt投影。付款的「captured」業務事實映射`finance.payment.status=paid`；退款結果未知映射`reconciliation_required`並保留provider狀態。稿件`frozen_at`與`ready_to_sign`搭配表達凍結，不另外發明frozen列舉。AML業務放行permitted對應`business_clearance.decision=approved`，blocked/restricted依pending/denied與限制欄位呈現，不另造status。客戶維護Pool的claimed/available為assignment與claimed欄位投影。共用映射以[資料字典](../../database/data-dictionary.md)及[API schema](../../api/openapi.yaml)為準；若契約真的不足，先送契約變更，不能在本模組私增列舉。

## 7. 資料責任、識別與留存

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| research.candidate | 本模組權威寫入 | Pantheon Persona 自主研究或人員研究候選輸入；非正式對客報告。 | producer,external_candidate_id,external_version | internal | research |
| research.candidate_evidence | 本模組權威寫入 | 候選報告採用來源快照、時間與授權範圍。 | candidate_id,evidence_ref | internal | research |
| core.object_asset | 受控共用基礎 | 加密版本化物件儲存引用；禁止內嵌原件或永久公開 URL。 | storage_key,object_version | internal | per_asset |
| core.inbox | 受控共用基礎 | consumer 去重與 event fingerprint；同 ID 不同 payload 必須拒收。 | consumer,producer,event_id | internal | integration |
| core.outbox | 受控共用基礎 | 與業務異動同一 DB transaction 提交的待送事件。 | event_id；producer,aggregate_type,aggregate_id,sequence_no；producer,idempotency_key | internal | integration |

完整欄位、型別、required、PII與CHECK在[database/catalog.json](../../database/catalog.json)。PK/FK含tenant_id；金額使用Decimal/numeric，API為十進位字串；實際時點用UTC，報表月份用Asia/Taipei。歷史證據使用不可變版本與物件hash，不修改已簽、已送、已記帳內容。法定留存分類不是任意刪除天數；有效legal hold禁止清理。

## 8. API 契約與整合測試入口

| operationId | HTTP／路徑（基底 /api/v1） | Request schema | Response schema | 角色代碼 | 版本控制 |
| --- | --- | --- | --- | --- | --- |
| ingest_research_candidate | POST /research-candidates | CandidateCreate | ResearchCandidate | pantheon_adapter | 依OpenAPI |
| list_research_candidates | GET /research-candidates | — | ResearchCandidatePage | analyst,analyst_reviewer | 依OpenAPI |
| get_research_candidate | GET /research-candidates/{candidate_id} | — | ResearchCandidate | analyst,analyst_reviewer | 依OpenAPI |
| validate_research_candidate | POST /research-candidates/{candidate_id}/validation | CandidateValidationRequest | ResearchCandidate | research_intake_service | If-Match 必須 |
| reject_research_candidate | POST /research-candidates/{candidate_id}/rejection | ReasonRequest | ResearchCandidate | analyst_reviewer | If-Match 必須 |

OpenAPI是wire contract權威；完整required欄位、錯誤、headers、auth與response均在[openapi.yaml](../../api/openapi.yaml)。修改命令依contract帶Idempotency-Key；相同key不同body拒絕，相同body回原receipt。CAS命令要求If-Match；過時版本回412，不做最後寫入者覆蓋。返回202只代表受理，不得在UI解讀為已完成。

## 9. 事件與一致性

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| research.candidate_received | 發出／接收 | M05 | M05 | research_confidential | 1.0.0 |
| research.candidate_validated | 發出 | M05 | M06 | research_confidential | 1.0.0 |
| research.candidate_superseded | 發出 | M05 | M06,M07 | research_confidential | 1.0.0 |

事件名稱與payload只採[registry](../../events/registry.json)及各JSON Schema；上表同時為producer/consumer整合責任。未列出在事件registry的流程訊號透過已有API查詢或受控排程實作，不自造隱藏topic。事件at-least-once；inbox按consumer/tenant/event_id去重；同一aggregate的sequence不符時等待／重建投影，不能默默跳號。不能把raw KYC、AML或研究全文放一般event payload。

## 10. UI/UX 與互動

| 畫面／區塊 | 資料與操作 | 狀態／限制 |
| --- | --- | --- |
| 候選收件佇列 | 來源、Persona、pool、標的、研究時間、收到時間、available_at、hash、validation狀態 | 可重查／退回並附理由；沒有直接對客戶發布鈕 |
| 證據檢視 | 每來源引用、授權scope、擷取時間、雜湊驗證、缺失與過期旗標 | 只提供可授權讀取的證據；未知授權顯示待補，不展示給客戶 |
| 對接健康 | 來源延遲、schema版本拒絕、重複率、DLQ數 | 告警只含source/id，不把研究全文與客戶資料送到普通log |

共通必備畫面狀態：loading、空集合、無權限、資料源失效／過期、版本衝突、處理中、成功及可重試失敗。可用動作由server決定；禁用按鈕須指出可操作的原因。長任務以job/receipt查詢，不用無限spinner假裝同步完成；表格支援鍵盤、可辨讀狀態文字及高對比，不能只以顏色表達。

## 11. LLM、自動化與人工決策

- 可以自動化：LLM 可把候選摘要分類、標示可能缺證據；不得把分類結果當正式投顧建議。
- 必須受控：來源認證、schema/hash、時間順序、授權scope、去重與review case建立由確定性adapter控制。

保存model/prompt/template/ruleset版本、來源refs與人工採用／修改紀錄。LLM輸出是不可信資料，不得拼成SQL、命令或未授權工具操作；遇無證據、越權、低信心與政策不明轉工作佇列。降低人力的目標是自動收件、比對、去重、摘要、提醒、對帳與例外聚焦，不是刪除需負責人承擔的決策。

## 12. 錯誤、失敗重試與操作邊界

- 同一source_event_id重送：回receipt與原candidate，不再建立case。
- 相同artifact version但hash不同：409/conflict quarantine，保留雙方證據並告警。
- 資料源future timestamp、available_at晚於當時建議：阻擋回填為當時可得的研究。
- Pantheon暫時不可用：本地待驗證狀態仍可查，不回傳空資料假裝沒有候選。

通用錯誤應含可穩定識別的code、correlation_id與可否重試，不能帶secret或原始個資。actor無權回403／對存在性敏感採一致回應；前提不成立422；idempotency conflict409；版本衝突412；外部依賴失敗503並保留進度。財務、簽章、訊息的unknown結果先查外部狀態，不以timeout推定失敗。

## 13. 可平行開工的依賴替身

- 外部 Pantheon：版本化 bridge contract，不能假設其現有 strategy approval等於投顧覆核
- M06 review queue
- M07 report source lineage
- M14 audit evidence traversal

以上是**整合依賴**，不是開工前置。使用[mocks/fixtures.json](../../mocks/fixtures.json)與[mocks/server.py](../../mocks/server.py)提供API替身；用[事件examples](../../events/examples)重播事件；DB測試使用[synthetic seed](../../database/seeds/001_synthetic.sql)。替身必須可注入success、denied、timeout、duplicate、out_of_order、stale_version。本次外部邊界內可由本平台adapter＋contract mock完成本地驗收；日後真實接線需另外驗證，不代表本次須到外部補程式。通過mock不代表已打通Pantheon/LegendFlow/簽章/金流。

## 14. 逐案驗收

本節是待實作與待執行的測試規格，不是通過紀錄。每個案例同時存在[機器可讀catalog](../../qa/test-catalog.json)與[追蹤矩陣](../../qa/traceability.csv)。

### UAT-M05-001 · Persona 可自主建立候選

- 前提：沒有 analyst ticket，但合法Persona/pool/artifact refs
- 操作：送入candidate
- 預期：建立immutable candidate與review待辦；不要求先由分析師建研究
- API：`ingest_research_candidate`
- 證據：`research.candidate`、audit/correlation與適用外部receipt。

### UAT-M05-002 · 分析師發起成果走同一通道

- 前提：origin_type analyst且來源合法
- 操作：送入candidate
- 預期：同樣驗證與建review；不跳過證據、覆核或簽章
- API：`ingest_research_candidate`
- 證據：`research.candidate`、audit/correlation與適用外部receipt。

### UAT-M05-003 · 候選重送只建一覆核案件

- 前提：同source_event_id/payload hash
- 操作：傳送兩次
- 預期：一次inbox applied，第二次duplicate；同candidate/review id
- API：`ingest_research_candidate`, `list_research_candidates`
- 證據：`research.candidate`、audit/correlation與適用外部receipt。

### UAT-M05-004 · 相同版本不同hash進隔離

- 前提：來源已有artifact v3 hash A
- 操作：再送v3 hash B
- 預期：衝突／quarantine；A不被覆寫；不能進簽章佇列
- API：`validate_research_candidate`, `reject_research_candidate`
- 證據：`research.candidate`、audit/correlation與適用外部receipt。

### UAT-M05-005 · 缺少必要證據不可ready

- 前提：artifact缺核心evidence refs
- 操作：驗證候選
- 預期：指出缺失並保持quarantined；有raw來源receipt供追查
- API：`validate_research_candidate`
- 證據：`research.candidate_evidence`、audit/correlation與適用外部receipt。

### UAT-M05-006 · 外部策略approved不當正式投顧核准

- 前提：Pantheon registry artifact marked approved
- 操作：收件並查客戶feed
- 預期：只能進review；member feed不存在該候選
- API：`get_research_candidate`, `get_member_feed`
- 證據：`research.candidate`、audit/correlation與適用外部receipt。

### UAT-M05-007 · 研究時間不得倒填交易時間

- 前提：Persona trade早於candidate產生
- 操作：收candidate並生成時間線
- 預期：保存trade/source/report/received各真實時間，不把report time設為trade time
- API：`ingest_research_candidate`
- 證據：`research.candidate`、audit/correlation與適用外部receipt。

### UAT-M05-008 · 未知schema版本明確拒絕

- 前提：envelope schema_version=999
- 操作：送入候選
- 預期：422 unsupported_schema，保存安全receipt；不默認忽略新必要欄位
- API：`ingest_research_candidate`
- 證據：`research.candidate`、audit/correlation與適用外部receipt。

### UAT-M05-009 · 外部證據URL阻擋SSRF

- 前提：evidence URL指向metadata/link-local/internal未允許位址
- 操作：取證據
- 預期：拒絕allowlist之外的地址與重新導向；不對任意URL發請求
- API：`validate_research_candidate`
- 證據：`research.candidate_evidence`、audit/correlation與適用外部receipt。

### UAT-M05-010 · 授權不足的來源不可客戶發布

- 前提：evidence license_scope只供內部
- 操作：候選移交報告工作台
- 預期：標明customer_distribution不允許；需替換或取得權利，不自動公開全文
- API：`validate_research_candidate`
- 證據：`research.candidate_evidence`、audit/correlation與適用外部receipt。

### UAT-M05-011 · 新候選版本通知舊review過時

- 前提：v1尚在覆核，v2正式到達
- 操作：處理superseding candidate
- 預期：v1保留；M06收到具版本事件並標示stale/rebase，不自動簽v2
- API：`ingest_research_candidate`
- 證據：`research.candidate`、audit/correlation與適用外部receipt。

### UAT-M05-012 · Pantheon故障不丟棄候選

- 前提：received已落地但遠端fetch timeout
- 操作：worker重試耗盡
- 預期：可在failure queue查與重送；一次成功後不重建case，原收到時間不變
- API：`validate_research_candidate`, `get_research_candidate`
- 證據：`research.candidate`、audit/correlation與適用外部receipt。

## 15. 可觀測性與營運交付

每個命令保存actor、tenant、purpose、aggregate/version、correlation/causation、policy/schema、result及必要原因。提供成功率、拒絕率、P95處理時間、queue age、重試／DLQ與依賴健康，使用module與error class標籤，避免customer_id高基數與明文PII。每個持久化job有lease、attempt、next_attempt_at與人工重驅原因；操作runbook需示範一個真實本模組失敗與恢復案例。

## 16. 模組完成條件

- 第3節全部REQ有實作、API／UI與正反路徑測試，不能只交資料表或畫面。
- 第8節所有API符合schema/權限/錯誤契約；第9節producer與consumer均通過雙向契約測試。
- 本模組UAT與所參與E2E有實測證據；未執行保持NOT_RUN，不把規格存在當測試通過。
- 敏感資料分級、SoD、不可變歷史、審計、重試、備援與操作手冊完成；沒有用真PII或secret測試。
- 未決商務／供應商項以版本化配置與明確生產門檻處理；不得刪功能、偷偷改為稍後再做，或替使用者批准尚未決定的獎金政策。

## 17. 開發入口與來源

- [本模組HerdR任務包](../../agents/task-packets/M05-pantheon_intake.md)
- [GitHub issue #5](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/5)
- [總體SA](../02-SA.md)、[總體SD](../03-SD.md)、[流程](../05-workflows.md)
- [需求來源與決策範圍](../01-product-scope.md)：本單元將使用者已決定事項具體化為工程規格；未核定的法務條款、服務中斷定義與獎金交互規則不冒充已批准政策。
