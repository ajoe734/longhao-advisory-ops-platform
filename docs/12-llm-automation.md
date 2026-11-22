# 12｜LLM庶務自動化與人工例外機制

## 1. 自動化分類

LLM適合非結構化文字理解與草稿；確定性程式適合計算、狀態與權限；專業或財務決策由授權人員完成。減少人力的設計是例外工作台＋一次錄入多處引用，避免讓員工每個步驟重打資料。

| 工作 | 輸入 | LLM輸出 | 自動落地 | 人工責任 |
|---|---|---|---|---|
| KYC補件整理 | 脫敏表單、完整性結果 | 缺件摘要與問題草稿 | 儲draft、建task | 身份/適合度判斷 |
| 研究覆核準備 | 授權candidate/evidence | 差異、缺來源、矛盾清單 | 連回原文與版本 | 分析結論與簽章 |
| Servant修訂 | 分析師明確要求 | 新artifact/revision | 收候選、校hash、排review | 採用新版與重審 |
| CRM排序 | 合法互動摘要、有效服務、已解/未解問題 | reason codes、next-action draft | 建待辦候選 | 正式聯絡與轉換歸因 |
| 客服 | 去敏對話、核准知識庫 | 分類、摘要、回覆草稿 | 低風險FAQ可依核准白名單答覆 | 投資、退款、客訴與受限事項 |
| 對帳 | 金額差異代碼、脫敏交易引用 | 差異說明與建議匹配 | 進exception queue | 核銷/改帳與付款 |
| 稽核 | 授權manifest與控制結果 | 證據摘要、缺項清單 | 匯出草稿 | 稽核結論與改善核定 |

## 2. Gateway契約

LLM request保存request_id、tenant、task_type、purpose、input_ref/hash、classification、allowed_tools、prompt/model版本、budget、deadline。response為可驗證JSON：draft、citations、reason_codes、uncertainties、policy_flags、usage/cost、provider_request_id。用schema拒絕不合法欄位；摘要引用必須能解析到使用者當時有權查看的source。

default tools為read-only、scope-limited；工具service identity不能持有company_accept/signature/refund/export-payroll權限。模型輸出的「approved:true」只能是untrusted文字，不映射正式核准欄位。客戶或研究內文包含指令也不改工具策略。

## 3. 什麼可以全自動

收件去重、格式與完整性檢查、比對規則、對帳的完全相符匹配、依核准規則列工作項、重試/逾時、提醒、版本保存、製作證據包、按核定rate計算獎金、已核驗簽章觸發發布。這些由程式執行並可重現；不需要LLM決定是否應執行。

對客自動回覆僅限核准FAQ與流程資訊，需引用生效知識庫、無個股/個人化建議、不承諾退款/通過。若涉及投資判斷、金錢、身份爭議或不確定，回一般受理訊息並建人工case；不把整段敏感對話送給通用模型。

## 4. 版本、成本與回退

每個task type設token/cost/time上限與日預算；模型連續失敗用circuit breaker轉模板/人工queue，核心簽約、權限、付款、報告查閱不依賴LLM可用性。model/prompt更新用合成評估集比較，記版本；不能在未核准變更下使CRM分數或回覆政策突然變化。

cache只用不含PII的模板/公開資料或tenant隔離reference；不得跨客戶重用私人context。模型使用與供應商保存/訓練設定在採購與資料處理條件確認，不假設所有API預設不留存。

## 5. 評估與效果量測

評估集合至少涵蓋：正常申請、身份矛盾、AML敏感訊息、來源缺漏、舊研究、簽後改文、未讀報告但正常續訂、空聯繫搶獎金、退款未知、prompt injection、跨客戶reference與供應商失敗。量測citation可追溯率、結構正確率、重大錯誤率、人工採用/修改率、每案時間、每案成本與未解決例外。

效率目標以導入前後實測比較，沒有目前人工作業baseline就不宣稱省70%或只需一人。各工作台記處理時間與重工原因，使公司能調整自動化而不犧牲必要專業與內控。

## 6. 禁止的權限升級

LLM不能自動clear AML、核准適合度、冒名公司承諾、簽章、改已簽文字、任意開權限、批准獎金、改帳或釋出退款。它可以在正式審查前整理候選內容；簽章後由被授權的決定性服務自動發送，是不同的權限路徑。
