# API、事件與平行開發契約

版本：1.0.0。適用全部十四個模組，作為同時開發與整合驗收的基準。

## 1. 唯一契約來源與完成界線

|檔案|用途|使用方式|
|---|---|---|
|`api/openapi.yaml`|OpenAPI 3.1 的 request、response、權限及錯誤格式|產生客戶端型別、路由stub、合約測試；實作不得自行改名|
|`api/operation-catalog.json`|逐操作的模組歸屬、路徑、角色、CAS、狀態碼|HerdR工作包引用 `operation_id`|
|`api/schema-index.json`|schema清單|檢查生成型別是否齊全|
|`events/registry.json`|事件生產者、消費者、分類與schema位置|依事件建立producer/consumer合約測試|
|`events/schemas/`|每事件的封閉 JSON Schema 2020-12|寫入outbox前、接收inbox時均驗證|
|`events/examples/`|完全合成的合法形狀示例|不代表真實KYC放行、簽章、款項或供應商連線|
|`mocks/fixtures.json`|所有API操作的合成request/response|每個模組不必等待其他模組後端完成|
|`mocks/server.py`|本機HTTP mock|可練習成功、缺權限、舊版本、逾期、政策未核准與外部失敗|
|`scripts/validate_contracts.py`|OpenAPI、ref、正反案例及覆蓋檢查|CI必跑，非生產功能完成證明|

正式營運實作仍須完成身分驗證、資料庫交易、業務狀態轉移、供應商串接、證據保全與全流程測試。`example.invalid`、mock bearer、合成簽章字串不得帶入staging或production。

## 2. URI、命名、型別與時區

- API外部前綴為 `/api/v1`。文件中的path已去除前綴。版本號的v1表示平台契約，不表示外部Pantheon或LegendFlow原生版本。
- resource與輸入均使用 `snake_case`。不可在相同版本交替使用舊草稿的 `customerId` 和新的 `customer_id`。
- 主鍵UUID由server生成；body中的其他UUID必須逐一驗證tenant與資源關係。tenant來自可信JWT／integration key映射，不能由任意body覆蓋。
- 所有系統時間用UTC RFC3339 `Z`，UI轉為Asia/Taipei。財務期間用明確日期，時區截點由認列政策決定。
- 金額使用`{amount: "123.450000", currency: "TWD"}`；比例也是十進位字串，禁止浮點算帳。數量及版次為整數。
- 不同的 `occurred_at`、`received_at`、`signed_at`、`published_at`、`delivered_at`保留原意。Persona交易時間不可當成事後報告或對客發布時間。
- 所有公開輸入schema預設`additionalProperties: false`；未知字段拒絕，不偷偷忽略「admin_override」等字段。
- 自由文字只能進入指定受限資料表／物件；不得直接串SQL、執行LLM回傳的工具參數或當URL下載指令。

## 3. 身分、授權與資料最小化

|身分類型|驗證與scope|不可授權的行為|
|---|---|---|
|客戶|LegendFlow BFF交換之可信JWT；後端從sub解析customer_id；客戶只能自己的資源|不能自行宣稱公司同意、KYC/AML通過、已付款或開通服務|
|員工|OIDC JWT、MFA、有效從業資格、role+resource scope+狀態+SoD|系統admin不能自動獲取AML/財務業務權限；業務不能查看所有客戶|
|系統服務|短效workload JWT、明確audience及作用scope|不能偽裝分析師簽署；自動發布只能消費已驗證簽章|
|外部callback|供應商原生驗簽由專屬adapter處理；標準內部adapter信封另用key隔離|不能僅憑body裡`verification_result`或`paid`就放行|

`x-allowed-roles`為操作起點，須與權限矩陣及職務分工併用；`allowed_actions`是後端經完整判斷的結果，UI不能自行推算。customer與staff共用操作時，必須分支檢查「本人」或「受權員工代辦」，留下實際actor、代辦原因與授權範圍，禁止直接冒用customer token。

敏感查詢採404避免洩漏不在scope內資源存在。AML詳細案件、名單命中、EDD調查與申報不進一般CRM；跨模組只允許安全的`BusinessClearance`決策與有效期間。

## 4. HTTP成功與錯誤

- `201`：本平台資源已持久建立。
- `202`：本平台已持久接收任務；不能顯示「簽章完成」「訊息送達」「退款完成」。以後續資源查詢及事件確認結果。
- `200`：讀取或同步命令已提交。
- `400/422`：語法／schema／業務前提失敗，不重試原樣資料。
- `401/403/404`：認證／授權／資源不可見；client不循環重試。
- `409`：業務狀態、冪等payload、尚未核准政策或重複轉換歸因衝突。
- `412`：版本不符；重讀最新版本，顯示差異，由使用者重作意圖，不自動覆寫。
- `428`：缺少If-Match或必要冪等前提。
- `429`：依Retry-After與退避處理。
- `503`：依賴不可用。`retryable=false`且code為`DEPENDENCY_OUTCOME_UNKNOWN_QUERY_FIRST`時必須先查供應商狀態；不能盲目重付或重送。

錯誤採Problem物件：`type/title/status/code/correlation_id/retryable`固定，`field_errors`可帶字段代碼；禁止含完整證件、AML原因、存取票證或stack trace。正常回覆也含X-Correlation-Id。

## 5. 並發控制、冪等與交易

### 5.1 If-Match

既有aggregate命令必須帶強ETag，例如`If-Match: "7"`。CAS目標是路徑主aggregate；例如`POST /reports/{report_id}/versions`鎖report版本，`POST /report-versions/{report_version_id}/reviews`鎖report version。不可只在UI比較；SQL update必須有tenant/id/version條件並確認影響列數。

兩人同時審同一報告、同時領取公司池客戶、同時覆核退款，只有符合版本與權限的交易成功。審查人送出的精確內容hash與manifest hash也必須一致；資源version相同不代表來源版本仍有效，須一起檢查來源lineage。

### 5.2 Idempotency-Key

每個非GET命令都需要key。server以`tenant + principal/integration + operation_id + key`為scope，保存請求canonical hash與原始結果。相同key、相同payload返回原結果與`Idempotency-Replayed: true`；相同key、不同payload回409。

金融交易、簽章、報告發布、計獎與帳務匯出另有持久business uniqueness；HTTP重放cache到期不代表可以重複扣款或過帳。付款、退款、會計匯出使用原業務識別與供應商冪等鍵，禁止每次timeout生成新鍵。

### 5.3 交易與外部副作用

同一資料庫交易寫業務狀態、immutable evidence引用與outbox；成功commit後worker才呼叫外部。HTTP回覆丟失可安全重查。worker使用lease及期限、checkpoint、bounded retries；外部已接受但本地未記錄時先查供應商reference再判斷續作。

模組不可透過API逕改其他模組資料表。共用查詢投影可複製必要狀態，但命令必須呼叫authority或消費其事件。讀取投影要有`as_of`與staleness，來源斷線不能回空陣列假裝「無案件」。

## 6. Cursor與清單

清單預設50，最大200；server以`created_at,id`或明確排序key建立穩定游標，包含tenant、filters、sort、snapshot時間與完整性簽章。續頁不得換tenant、filter或排序。回覆`next_cursor=null`表示結束；`total_estimate`可省略，不能把不精確數量包裝成確切總數。

客戶搜尋、模糊字串及時段filter須列入各模組端點的明確schema後才可增加；不得提供任意SQL、任意字段排序或全表無範圍匯出。

## 7. 事件信封與分類

每個schema都明確定義`event_id/event_type/schema_version/tenant_id/producer/aggregate_type/aggregate_id/aggregate_version/sequence_no/occurred_at/emitted_at/correlation_id/causation_id/idempotency_key/classification/payload_sha256/payload`。

- `event_id`：事實事件唯一ID，retry不改。
- `aggregate_version`：事件描述的業務版本。
- `sequence_no`：同tenant、aggregate內順序；不代表全平台總順序。
- `correlation_id`：一條業務旅程；`causation_id`：觸發本事件的上游事件，最初操作可null。
- `payload_sha256`：RFC8785 canonical JSON的SHA256。提供的合成payload只含字串、整數、bool、null及物件／陣列，驗證工具可用穩定排序JSON驗證這組fixture；正式實作使用經測試的canonicalization函式。
- 分類為`internal/customer_confidential/research_confidential/aml_restricted`。broker ACL、consumer scope、儲存權限及retention必須同時執行；加一個欄位標籤不等於隔離已完成。

## 8. 事件排序、重播與失敗

採至少一次投遞；不宣稱跨PostgreSQL、LINE、SMS、簽章商與金流具端到端exactly-once。

1. Producer在同交易提交狀態與outbox，dispatcher以lease領取未發布事件。
2. Consumer先驗schema版本、來源scope、tenant、payload hash，持久保留inbox receipt。
3. `(consumer,tenant_id,event_id)`唯一；相同ID不同hash為衝突事件，隔離並告警，不能當成功duplicate。
4. Consumer落地業務變更、inbox結果及下一個outbox於同交易。
5. 失序事件不得倒退狀態。完整訂閱該aggregate事件流的consumer可偵測sequence缺口並查source；只訂部分event types的consumer不能把跳號直接認定遺失，需按aggregate version／查authority確定是否可套用。
6. timeout採指數退避與jitter，超過可接受次數進dead-letter。redrive需要actor、reason、原event_id與payload hash；重新驗證發布有效期限、簽章撤銷及當下客戶權限。
7. 撤回、到期、退款或停權即使比queued delivery晚到，也要在dispatch前再讀authority；不能只相信早先受眾快照。

事件版本採明確registry。封閉schema增加字段也會被舊validator拒絕，所以不能宣稱「新增optional欄位就一定相容」；producer升版前完成雙版本consumer或adapter translation，升版過程保留N/N-1測試。

## 9. 研究、報告、建議與簽章關係

Persona可先自主產生candidate，操作自己的pool與研究並不等同投顧發布。M05僅接收來源與證據快照；M06完成分析師／Servant修訂與版本追蹤；M07產生正式投顧報告版本。

初建順序允許draft recommendation先引用candidate而尚未有report version。建立report version時原子建立recommendation與report版本關聯；**freeze以前**必須完成雙向依據及審查，不能拿草稿空關聯簽章。

簽章包包含：報告檔內容hash、來源artifact與evidence snapshots、建議版次、各渠道實質文字及hash、受眾規則版次及hash、有效期限、必需審查決策。`report_content_sha256`、`bundle_manifest_sha256`、`signed_file_sha256`是不同欄位，禁止用一個hash代替三者。簽章商回呼只是待驗證證據；M07核對身份、資格、session、hash與所需審查後，才能提交`report.signature_verified`。

M08收到此事件後立即自動建立publication；沒有第二個人工`POST /publications`。M09執行feed與渠道投遞，保留accepted/sent/delivered/read分別證據。簽後LLM不得生成新的投資判斷或實質訊息。

## 10. 全模組平行實作交付

十四組人員/agent均可從同一契約commit開發。允許修改的repo只有longhao-advisory-ops-platform；Pantheon與LegendFlow只預留規格或在本repo實作adapter，禁止修改、PR或部署外部專案。若上游尚未實作，使用此repo mock；mock測試通過只是模組接線完成，不能當整合或營運驗收。每模組PR要附producer/consumer contract tests、真實DB交易與授權測試；外部接通證據僅在已有可用介面時附上，否則明列待接通並仍完成本平台十四模組的開發與本地驗收。

任何破壞性契約變更須提交affected operations/events與所有consumer清單、migration與雙版本策略、對應工作包及測試。共享檔由整合負責人合併，模組branch不能各自無聲改同一API名稱。

## 11. 可執行驗證

```bash
python scripts/validate_contracts.py
python mocks/test_server.py
python mocks/server.py --port 8765
```

依賴：PyYAML、jsonschema、openapi-spec-validator。只做離線開發，不需要真實token。mock使用方法見[mocks/README.md](../mocks/README.md)。

## 12. 標準參考

- [OpenAPI 3.1.0規範](https://spec.openapis.org/oas/v3.1.0.html)
- [JSON Schema 2020-12](https://json-schema.org/draft/2020-12)
- [RFC 9457 Problem Details](https://www.rfc-editor.org/rfc/rfc9457)
- [RFC 8785 JSON Canonicalization](https://www.rfc-editor.org/rfc/rfc8785)

這些標準定義格式；本文件列出的業務角色、覆核及放行規則是本專案設計，不能僅憑標準相容性推論法規已完成。

## 13. API與資料庫投影

[api/database-projection-map.yaml](../api/database-projection-map.yaml)列出authority table、欄位轉換及複合查詢。API的state、contract_id等不必是同名單表欄位；必須由指定authority及快照產生。`report_content_sha256`對應DB`content_sha256`、`bundle_manifest_sha256`對應`bundle_sha256`，兩者不可混用。簽章事件的aggregate_id必須等於payload.report_version_id。
