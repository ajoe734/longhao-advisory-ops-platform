# 07｜帳務、財會、AML/CFT與內控制度實作

## 1. 財會範圍及總帳邊界

本平台保存營運子帳、履約收入排程、款項/退款與獎金來源證據，向公司核准會計系統輸出對應分錄/憑證。若會計系統未選定，提供版本化GL adapter與平衡檢查；不假稱本平台已取代完整財務報表、稅務申報與法定帳簿系統。

| 物件 | 代表事實 | 不能混用 |
|---|---|---|
| 契約/服務期 | 承諾與履約範圍 | 不等於已收款 |
| bill/order | 應收與價金條件 | 不等於稅務憑證或收入 |
| payment | 客戶付款/支付受理結果 | 不等於銀行已清算 |
| settlement | PSP批次結算、費用與淨額 | 不等於各期服務收入 |
| bank entry | 銀行入帳/出帳 | 不可忽略與payment的配對 |
| revenue recognition | 已提供服務可認列金額 | 不等於年度契約現金總額 |
| refund / credit | 實際退錢與收入/負債調整 | 兩種效果分開核對 |
| commission | 依政策計算的獎金成本/應付 | 不可重複沖減服務收入 |

## 2. 收款與對帳流程

M02產生service agreement與價金reference；M14建立bill與payment intent，客戶在PSP受控頁付款，平台只存token/reference，不存完整卡號/CVV。callback驗簽去重，只更新允許的狀態；如客戶頁回跳成功但callback未到，顯示processing並查provider，不直接active。

每日匯入PSP明細及銀行對帳，以provider transaction、settlement batch、amount/currency和時間等決定性條件配對。不同金額/重複/多對一/不明入款進exception queue。LLM可解釋可能原因，不能自動核銷金額差異。手續費與應收、營業收入分開；net bank amount不當commission base。

每個batch有hash、來源期間、匯入actor、重複偵測、match結果與核決。相同檔重傳不建立第二批；修改過檔案要新version與差異，不能偷偷覆寫。

## 3. 履約與收入

服務期間用半開日期/時間區間；排程由財會核准policy決定每日/月或履約事件認列。審查前收款及未生效未履約服務保存預收/待處理負債。開權限時間、暫停、延長、終止與服務中斷皆可影響schedule，但不可由客服隨意改財會已關帳收入。

累計已認列不超過該服務核定可認列金額；period uniqueness避免排程重跑重複認列。收入事件含source contract、period、gross/tax/untaxed、currency、policy、original/adjustment refs與confirmed_at。正式獎金僅消費財會已確認版本。

示例（純未稅假設）：年服務價金12,000，前兩個月已履約2,000，未履約10,000。若退未履約10,000，減預收，已履約獎金base不變；若另退已履約500，收入追加-500並對應獎金沖回，不將整筆10,500都從已計獎2,000再扣一次。

## 4. 稅與憑證

保存服務價金、是否含稅、稅別/稅率版本、稅額、未稅額、憑證型態、外部憑證號及作廢/折讓引用。沒有正式財會設定不預設除以1.05或1.02，金流手續費不能當稅額。

發票/收據與稅務介接依實際營業型態、會計政策及供應商規格設定；開發先用多種稅型fixture覆蓋含稅、未稅、零稅/免稅或其他核定類型，但不能以fixture當稅法判斷。正式啟用需財會核定 BIZ-FIN-01。

## 5. 退款與防重複

客服/契約作業提出理由與範圍→財會計算可退額/履約影響→不同人覆核→authorized service呼叫PSP→收回執/查詢未知→銀行/settlement對帳→會計與獎金調整。退款可多次但累計不可超過可退餘額；並行請求在資料庫鎖與unique外部key下驗餘額。

逾時不代表失敗。status unknown進reconciliation，未查證不得換key重退。部分退款、跨月退款、chargeback和取消退款有獨立reason與ledger events；既有signed contract及已送研究不因退款刪除。退款結案要財會、provider、bank effects均解釋一致。

## 6. 月結與總帳輸出

流程：收件完整性→支付/銀行對帳→預收與履約核對→收入排程確認→退款/credit→commission statement→分錄平衡→覆核關帳→GL export receipt。每步可以自動跑檢查，異常入工作清單。

GL export以batch+mapping version+hash識別，debits=credits，currency一致，科目/部門/服務引用不缺。外部接受未知先查batch，不重複產憑證。關帳後只能新adjustment進允許期間；重開關帳需理由與核准，所有下游影響列diff。

## 7. AML/CFT完整控制

包括身份/實益人/代理與業務目的資料、風險分類、適用名單/PEP/制裁/資恐篩檢、可能命中處理、較高風險的EDD與主管核決、資料更新與持續監測、事件例外、可疑事項限制流程、留存與受控稽核。個人與法人欄位按適用範圍啟用；不要求所有人一律填無關法人欄位。

客戶線上聲明與公司篩檢結論分開。provider unavailable或名單過期不是clear；AI相似度/摘要只是case輔助，不能clear alert。可能命中不自動對客說明原因，也不把限制案件完整丟進CRM。內部申報、主管核決與外部申報回執按專責權限保存；不能由普通營運或LLM自動提交。

監測規則應符合投顧服務的實際付款/退款/身分/使用型態，不照搬銀行所有交易規則；異常第三人付款、頻繁退款、身分不一致可成case線索，不等同法定可疑結論。命中/排除理由、list version、檢索條件與審閱人必須可復核。

## 8. 內控與職責分離

| 控制 | Maker | Checker/Owner | 證據 |
|---|---|---|---|
| 公司承諾 | 契約作業 | 授權公司承諾者 | 最新gate快照、版本、身份 |
| AML高風險/命中 | AML承辦 | AML主管 | 證據、排除/限制理由與決策 |
| 報告覆核簽章 | 研究覆核/分析師 | 依正式review policy的必要角色 | exact bundle、各review與簽章 |
| 退款 | 財會製單 | 不同授權覆核者 | 可退餘額、原款、回執 |
| 獎金結算 | 系統試算/財會 | 主管/財會覆核 | 原收入、歸因、policy、差異 |
| 權限異動 | 資訊執行 | 業務資料owner核准 | 申請、核准、生效與撤銷 |
| 稽核抽樣 | 稽核 | 稽核主管/治理程序 | 範圍、manifest、查核與改善 |

最少人力不表示同一人在同一交易製單與覆核。人員可否兼任依適用制度與公司核定；系統在動作層拒self-approval，不能靠UI勾選忽略。

## 9. 留存、資料權利與稽核包

依資料類別設定retention trigger（契約權利義務結束、報告提供、付款交易、案件結束等），minimum期間與legalhold；不同類別不以同一created_at加五年處理。法源對照在 `17-sources-compliance.md`，法遵核定具體schedule後啟用刪除。未核定不得自動刪除。

稽核包：manifest＋object IDs＋SHA256＋事件時間＋actor/authority snapshot＋契約/研究/訊息/收入/獎金關聯＋驗證報表。匯出依scope核准、敏感內容最小化、下載短效並留痕；archive使用公司控管儲存，不放public Git。

## 10. 操作例外清單

必要佇列包含：未對帳款、未明退款、收入排程差異、GL拒收、獎金爭議、過期審查、AML provider故障、未完成簽章、未發/部分失敗通知、retention hold衝突及稽核缺失。每項要有owner、SLA、retries、最後原因及下一步；不能以讓人每天翻log代替管理工作台。
