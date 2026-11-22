# 外部系統串接與責任邊界

版本：1.0.0。這是龍好營運平台的完整目標串接規格；Pantheon、Agora/Servant與LegendFlow維持獨立系統，十四個內部模組同時依此契約開發。

## 0. 使用者明確的修改範圍

本次以及交給HerdR的開發，只能修改`ajoe734/longhao-advisory-ops-platform`。Pantheon與LegendFlow只預留串接規格，最多在本repo實作對接adapter、mock與合約測試；不得為完成本平台功能去修改這兩個外部repo、提交外部PR、改設定或部署它們。Agora也維持外部邊界。

未取得實際外部介面時，十四個模組仍同時完成本平台程式、DB、UI、adapter port、合成測試與整體本地驗收。以`implemented_mock_verified / provider_contract_available / provider_connected_verified`分別標示狀態，不把外部接通當成其餘模組停止開發的前置。取得合法可用之測試介面時，才在不改動外部專案的前提下執行連通驗證。

## 1. Authority矩陣

|對象|外部負責|龍好平台負責|禁止混同|
|---|---|---|---|
|LegendFlow|官網、顧客登入與客戶端UI、表單／成員介面|客戶主檔、申辦、公司承諾、服務權限、正式訊息feed與客服案件|前端顯示「付款成功」不能自行授予權限|
|Pantheon|Persona自主研究、交易pool、研究規劃、evidence/artifact與來源lineage|接收候選、正式投顧審查、簽章及發布證據|Persona買賣、策略核准與正式投顧建議是三件事|
|Agora/Servant|分析師對話、要求修訂、補證據與反方分析的研究介面|權限、review case、版本前提、正式決策與簽章|Servant不能成為簽章人或繞過發布控制|
|身分／簽章供應商|身分核驗、簽署ceremony、簽章／時間證據服務|核准使用方法、資格／身份／內容驗證、保存證據與最後權限判斷|provider回呼不等於內部核驗已通過|
|AML篩檢商|受約定名單、PEP／制裁／資恐比對與版本證據|CDD/EDD案件、風險評估、裁決、覆核、限制與留存|客戶簽一張聲明不等於完成AML/CFT審查|
|LINE/SMS/郵件商|傳輸、可提供之接受／寄出／送達回報|收件者授權、內容版次、重試、到期、撤回與稽核|accepted不等於delivered；沒有read回報就維持未知|
|金流／銀行|支付、退款及清算證據|對帳、預收、履約收入、退款分攤與會計匯出|收款、清算和收入認列分開|
|會計／薪酬系統|核准會計過帳及薪酬實際發放|可重現收入／獎金明細、核准與控制總額、接收回覆|產生檔案不等於已入帳或已領款|

所有外部系統透過port/adapter隔離。內部操作、事件與schema見[API與事件](09-api-events.md)；`api/adapter-ports.yaml`列實作接口。此repo不新增Pantheon研究引擎、不複製LegendFlow網站、不重建Agora。

## 2. 已觀察證據與尚待驗證介面

- 業主已指定LegendFlow為客戶端UI：[legendflow.tw](https://legendflow.tw/)。網站存在不證明已具備本文件所列API；須由對方團隊確認目前技術棧、登入issuer、callback能力與部署環境。
- Pantheon於本次對齊使用的dev快照為`bac80d756dec67b19c9af5183eae7a9869835015`：[repo快照](https://github.com/ajoe734/pantheon/tree/bac80d756dec67b19c9af5183eae7a9869835015)。整合驗收須在選定版本執行，不以「dev最新版」作永久識別。
- 已檢閱的研究語意以[Registry contract](https://github.com/ajoe734/pantheon/blob/bac80d756dec67b19c9af5183eae7a9869835015/services/registry/contract.md)、[Foundation outbox](https://github.com/ajoe734/pantheon/blob/bac80d756dec67b19c9af5183eae7a9869835015/services/foundation/outbox.py)、[Reliable delivery](https://github.com/ajoe734/pantheon/blob/bac80d756dec67b19c9af5183eae7a9869835015/services/foundation/reliable_delivery.py)為參照。Registry artifact狀態`draft/candidate/approved/retired`與部署stage分開；不得拿舊SD的`approved_template`當現行權威。
- 尚未驗證Pantheon具有「正式投顧研究報告」專用artifact種類或本平台要求的report API。這裡定義的是龍好專屬advisory adapter；需由雙方mapping實作，而非直接聲稱現有endpoint可用。
- Agora/Servant前端以[execute-plans](https://github.com/ajoe734/execute-plans)為指定來源；具體deployment API與scope交換仍需在供應方staging驗收。歷史front-ai-trading-system不能直接視為現行前端。

原始研究系統某些Registry local mutation具有自己的transaction receipt；本平台採用outbox不代表Pantheon所有內部服務都採同一機制。Adapter只依已驗證對外契約，不能透過共享資料庫依賴其內部表結構。

## 3. 通用串接協議

### 3.1 端點、憑證與key輪替

- staging/prod各自base URL、issuer、audience、key、provider account與callback URL；不得共用token。模擬環境使用`example.invalid`及mock markers。
- key保存於秘密管理服務，記錄key id、啟用／到期時間與雙key輪替窗口；程式只拿reference。外部供應商不能讀客戶資料庫或內部簽章private key。
- 供應商原生webhook驗簽遵照已簽訂之provider contract；若其欄位格式不同，adapter先驗原始body，才轉成本平台標準輸入。不能先JSON重排再驗原生簽章。
- 本平台標準HMAC內容：`timestamp + \n + nonce + \n + METHOD + \n + request_path + \n + SHA256(raw_body)`，HMAC-SHA256轉小寫hex，constant-time比較。`request_path`含`/api/v1`且不含query；callback禁止query參數承載業務命令。
- timestamp預設±300秒；nonce重播窗口至少600秒。相同nonce相同已驗簽payload可回既有receipt，相同nonce異payload拒絕。nonce防重播與業務event_id去重是兩層，不能混為短期cache。

### 3.2 接收、回覆與重送

接收端先驗認證、body size、schema、hash、tenant映射與外部識別唯一性，再寫durable inbox。成功回202及receipt代表接收，並非業務已完成。未能durably接收不能回200假成功。

Callback未知版本、來源未授權、相同external_id不同hash或不合法時間順序要隔離。非重試錯誤回4xx；短暫依賴錯誤回503與Retry-After。供應商重送沿用原event_id；receiver不以來源時間近似去重。

### 3.3 檔案與URL

所有資料以受控object asset reference交換。檔案先進隔離區、檢查media type、大小與惡意檔，再成為可讀asset。外部artifact URL只允許配置的host與path前綴，防SSRF；不可因Pantheon/Servant產出的URL就任意抓內網。下載需記錄source hash、retrieved_at、資料使用授權與可用時間。

## 4. LegendFlow整合

### 4.1 客戶旅程

1. 客戶在LegendFlow登入；identity exchange驗證issuer/audience/nonce後，M01取得或建立單一客戶。不能只拿未驗證email就合併不同身分。
2. `create_application`選擇核准契約與服務代碼。服務規格來源是已核准的商業設定與契約，平台不另作任意促銷方案管理中心。
3. `create_customer_signature`先完成客戶簽署。合約顯示與實際簽署hash一致；customer_signed與company_accepted分開。
4. 再提供KYC、適合度、AML/CFT聲明與必要資料。表單可同一旅程完成，回答版本、聲明與原始證據各自留存。
5. 公司審查完成後M02判斷契約承諾、生效、付款及服務條件，再授予entitlement。尚待審、補件、拒絕、退款中均有獨立可理解狀態。
6. LegendFlow只讀安全進度；AML對客訊息僅「需要補充資料／審核中／未通過」等核准模板，不能洩漏內部調查或名單命中。

### 4.2 Feed與報告

LegendFlow呼叫`get_member_feed`，後端按當下customer/entitlement限制publication。深連結使用publication/report version識別，不能給永久公開object URL。報告票證最長120秒，兌換時再次檢查權限。

客戶主要消費即時訊息；不要求看完報告才能獲取一般服務。每條進場／更新／出場建議必須能追到當時已簽研究依據，報告閱讀率不足不能單獨當流失或適合升級的證據。

發布後不得由LegendFlow改寫實質投資內容；只可在受控版型呈現固定文字、時間與連結。更正顯示新版本及原版本更正標記，歷史仍可供稽核。

### 4.3 測試交付

雙方需提供：OIDC discovery或交換協議、測試issuer、callback IP/簽章需求、成員深連結pattern、CSP/CORS清單、登出與token撤銷、mobile重連行為、member feed快取失效策略與隱私log確認。CORS只允許配置origin，不能萬用origin加credentials。

## 5. Pantheon Candidate Port

### 5.1 輸入

`ingest_research_candidate`接收origin(`persona_autonomous/analyst_initiated`)、persona/pool引用、ticket/plan/artifact及版本、內容SHA256、evidence bundle引用、source_created_at/source_available_at、provenance snapshot與license scope。

Persona自主流程為正常來源；不要求分析師先點「開始研究」。龍好adapter收件後驗證內容hash、授權及時序，建立candidate與review case。未知artifact種類不可偷偷映射成「已核准投顧報告」。

### 5.2 修訂與過時

同source artifact+version+hash重送回同candidate；同external event_id不同hash隔離。新版本建立新candidate／lineage，發`research.candidate_superseded`，舊review標stale。正在簽署時新候選不能替換待簽內容；分析師需決定保留已凍結版本或建立新版本，依法遵政策再審。

來源研究中途失敗、資料過舊、授權過期、evidence不可取用均不能發客戶可見內容。UI顯示具體原因與最後可用時間；不以空白報告當成功。

### 5.3 完成判定

需以指定Pantheon版本完成fixture與staging事件往返，驗證external refs可解析、來源時間可靠、hash一致、licence scope可查、錯誤／重播不重建case。僅看repo程式或文件不能判定已上線可用。

## 6. Agora / Servant Port

`open_servant_conversation`攜帶review case、candidate、固定artifact與可讀evidence列表；不得附客戶KYC、AML或獎金資料。Servant對話使用專用scope，不給一般admin token。

`request_servant_revision`附base artifact版本/hash、instructions、required evidence及output類型。回呼需帶原revision_id、base版本、新artifact/hash與transcript snapshot；當前review已前進時回覆標stale，不能覆寫。

分析師可要求補證據、反向分析、重寫報告或訊息草稿。這些都是待審內容；所有修訂形成新版本。正式簽章、資格核驗、最終批准與publication authority留在龍好。

## 7. 報告覆核、簽章與即時發布

### 7.1 簽前完整包

建立report version時先計算candidate manifest並回傳hash供審查。manifest含報告內容、建議版次、各渠道文本、證據快照、受眾policy與有效期限。必要professional/compliance review綁定此精確hash；freeze是在review通過後確認無變動並鎖定狀態，不能等freeze才第一次建立審查內容hash。

M07對signature request驗證：簽署人有效資格、MFA與身份、審查完整、版本未變、授權／有效期限、SoD。供應商返回signed file、原內容hash、manifest hash、身份/證書/時間證據；是否使用特定CA或簽章級別由核准方法決定，不在設計文件假稱法規指定單一供應商。

### 7.2 驗證即觸發

M07確認provider evidence後，在同transaction保存verified signature及`report.signature_verified`outbox。M08立即依固定manifest建立publication與受眾快照，M09立即推送LegendFlow及核准渠道。**沒有額外人工發布鈕**。

自動化服務可以執行已核准、可重現的發布工作；LLM沒有發布權限。簽後不得新增或重寫實質內容，僅允許受控傳輸欄位如短效access token在不改變固定內容的情況產生。

### 7.3 異常

供應商timeout保持signature_pending並查原session。無效簽章、身份不符、證書／資格問題、hash不符、review過時均不發事件；保留失敗證據。撤銷用append-only revocation並發下游withdrawal；刪除原簽章或改簽署時間均禁止。

即時性以端到端latency metric檢測：verified commit→publication persist→LegendFlow可見→各渠道accepted。對外送達受渠道商影響，不能將本地queue速度當送達承諾。

## 8. 訊息投遞Port

每筆投遞以publication/customer/channel建立business uniqueness及供應商idempotency key；同report可支持多條仍有效建議，但每條message版本都需明確對應已審文字與研究依據。

Dispatch前再次確認：publication未撤回未逾期、signature仍有效、customer entitlement當下有效、收件渠道已驗證且可用、所用template及content hash符合已簽manifest。訂閱權限收回時不能因audience snapshot曾含客戶而繼續傳。

回報分別保留provider accepted、sent、delivered、failed；read_at只有供應商確實支援且有可信回報才填。SMS通常不具有閱讀證據；系統不可由點開或寄送推定讀取。失序receipt追加保存，投影按有效狀態次序和provider時間更新，不倒退已確認送達為accepted。

Timeout且是否送出不明時先query provider_message_ref；無查詢能力者進人工／受控調查，禁止無限制blind retry造成重複帶單。已過時建議不因DLQ redrive再次發出。

### 8.1 客服回覆的實際發送

M13的`add_case_interaction`只記錄已發生互動，不能代替送訊息。客服由`draft_service_response`取得草稿、以`get_service_response_draft`查看精確文字及來源，呼叫`approve_service_response_draft`取得server產生的核定紀錄，再以`approval_id`呼叫`send_service_response`；人覆核或低風險模板規則均不可由client自行捏造approval UUID。發送命令綁定draft hash、template版本、批准證據、案件customer的已驗證channel binding與有效期限。

此命令回202為queued。`get_service_response`與`receive_service_response_receipt`分別查詢及更新accepted/sent/delivered/failed/unknown；provider timeout先查原request/message id。不能以新增outbound interaction就顯示已送達。`support.response_status_changed`僅傳case/response/receipt識別與狀態，不攜正文或AML內情。

允許的service principal僅可使用核准的低風險固定模板；對話式LLM草稿、客訴處分或新投資判斷不可透過`approved_low_risk_template`標籤繞過人工／正式報告程序。對外渠道adapter只在本repo實作，不改外部專案。

## 9. 金流、財務與會計Port

- `create_payment_intent`金額、幣別、invoice/application與回調關係由server驗證；前端金額只是申請，不是可信應收。
- provider `paid`先保存payment，清算另以settlement/bank statement核對gross/fee/net/control count。
- 預收保持負債，按已核准服務期及認列policy產生revenue。獎金僅消費finance-confirmed收入與歸屬退款。
- 退款分開unearned prepayment與earned reversal。尚未進獎金basis的預收退回不能再扣一次計獎收入。原refund保留original payment、allocation、maker/checker與供應商receipt。
- 退款發送須用相同provider冪等鍵。timeout進reconciliation_required，先查再重送；未確認前不能回報已退。
- AccountingExport包括balanced debit/credit、mapping/tax版本、source IDs、service period、控制總額和hash。ack可部分成功，已接受journal不可重送成第二筆。
- 獎金payout export對應approved run與原recipient；負向調整不自動扣薪、不移到接手業務，另依正式制度處理。

## 10. LLM與業務庶務Port

LLM可分類客服、摘要互動、草擬核准知識回覆、整理補件差異、推薦CRM下一動作、彙整研究修訂。每次保存model/prompt/knowledge版本、輸入引用、輸出hash、審閱或規則放行證據。

一般LLM不得獲取AML調查／名單詳情、完整身分文件、簽章private key、退款執行token。輸出視為不可信資料，不能把「工具呼叫」直接當系統命令。涉及投資新判斷、客訴處分、適合度例外、AML裁決、公司承諾、正式簽章、退款與發放須由指定職責控制。

自動化可節省重複輸入、提醒、核對與排程；責任歸屬與高風險控制不因LLM代做文書而消失。

## 11. 雙邊驗收矩陣

|介接|正常案例|必須拒絕／恢復案例|完成證據|
|---|---|---|---|
|LegendFlow|先簽後KYC、公司承諾後權限、member feed|冒用customer_id、舊token、跨客戶report票證、撤權競態|雙邊contract test、瀏覽旅程與權限audit|
|Pantheon|Persona自主candidate、版本與evidence解析|未知schema、hash不符、stale artifact、source重送|pinned版本、payload/結果hash、case唯一性|
|Servant|基於固定版本修訂|晚到回覆、越scope證據、prompt指令注入|conversation/revision lineage、拒絕紀錄|
|簽章商|正確簽署精確manifest|冒名、失效、hash不符、timeout後重回呼|簽章驗證證據、publication唯一與時間鏈|
|篩檢商|清楚、可能命中、EDD案件|供應商掛掉不能當clear、名單版本缺失|受限case紀錄及safe clearance|
|訊息商|accepted/sent/delivered狀態|撤權、逾期、重複、provider結果不明|receipt原始證據與一次business dispatch|
|金流/銀行|付款、清算、退款|重複callback、短付、手續費差異、refund timeout|對帳exception與closed-loop證據|
|會計/薪酬|平衡匯出、接收ack|部分ack、重送、未核准政策|hash、控制總額、原source映射|

所有14模組從mock同時開發並交付完整本平台功能。上表涉及外部服務的證據在取得可用介面後才進行；未取得時在串接清單列為待接通，不要求修改外部repo，也不阻擋其餘模組的實作與本地整合。正式啟用某個對外渠道／金流前需完成該介面的真實驗證；mock不得冒充已接通。
