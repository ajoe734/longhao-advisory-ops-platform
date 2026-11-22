# 11｜身分、權限、職責分離與資訊安全

## 1. 授權模型

每個請求依可信issuer驗簽、aud、exp、nonce/session綁定取得actor與tenant；client傳的tenant_id/customer_id不是授權證明。員工使用公司IdP與MFA，簽章/公司承諾/財務核決採step-up與短效challenge；服務用獨立workload identity和audience，不共用員工token。

授權決策 = role permission ∧ tenant/object scope ∧ 有效任職與必要資格 ∧ resource state ∧ maker/checker ∧ purpose/classification。前端隱藏按鈕、知道UUID或能解密某個bucket都不等於有讀取業務資料的權限。

OpenAPI每個operation的 `x-allowed-roles` 與 `x-resource-ownership` 是可機器驗證矩陣；本文件說明額外條件。`config/rbac-matrix.csv` 從同一API產生，用於review與授權測試。

## 2. 人員與服務角色

| 角色群 | 允許範圍 | 重要限制 |
|---|---|---|
| member | 本人申請/簽署/表單/權限內feed與case | 不能讀別人的資料或AML內部case |
| cms_operator / contract_operator | 作業資料、文件與契約queue | 無自動company_acceptor權限 |
| company_acceptor | 最新gate合格時承諾契約 | 需有效授權與step-up；不能變更原始KYC結論 |
| kyc_reviewer / suitability_reviewer | 指派或範圍內評估 | 一般客服不具專業判斷權 |
| aml_officer / aml_supervisor | 受限篩檢、EDD與核決 | 主管核准與承辦分離，對一般系統只出gate |
| analyst / analyst_reviewer / analyst_signer | 研究、覆核、簽章 | 有效專業資格和指定覆核規則，不能憑系統admin替代 |
| compliance_reviewer / supervisor | 政策指定法遵review | 獨立於研究/財會/招攬等不相容角色，以正式制度核定 |
| sales / sales_supervisor | 可維護客戶與機會、活動、歸屬爭議 | 無AML全文/付款釋出；assignment不等於獎金 |
| marketing | 來源與cohort彙總、核准campaign | 不給全客戶KYC/AML，不建立外部介紹費權限 |
| service_agent / supervisor | 授權case與必要客戶摘要 | 投資/退款/AML由對應專責人處理 |
| finance_operator / checker | 子帳、對帳、退款與關帳 | same transaction maker不可checker |
| commission_operator / approver / payroll_operator | 試算、核決、發放介接 | 核准policy才可發放；薪資追償另核決 |
| internal_auditor | 範圍授權的readonly證據 | 無改帳/改報告/改case決策權 |
| adapter / worker roles | 各自endpoint與任務scope | 無人工決策權、不任意跳別的module |

相容role群不等於一定可由同一人兼任。聘任/資格核定由公司制度與適用法規決定；程式在授權policy校驗禁止組合和有效日期。

## 3. 欄位級與資料級控制

Customer360一般顯示遮罩聯絡、有效契約、狀態和owner；身分證件/財務問卷要獨立權限和purpose。AML case/possible-match/STR詳情只進restricted schema、logs與keys，不送CRM、客服或Pantheon。研究人員只得適用研究說明需要且經授權的摘要，不取客戶完整身分附件。

資料庫RLS只在非superuser且非BYPASSRLS的runtime角色成立。app transaction用server端設定 `SET LOCAL` tenant/actor，connection pool歸還前確保沒有殘留；跨tenant FK防止錯link。migrator、backup與runtime identities分離，應用不可用DBowner。

## 4. Maker/checker強制規則

不能自己核准自己的退款、commission statement、GL關帳或權限升級。review policy可要求作者/覆核人分開；角色多重持有仍要檢查actor ID是否相同。核決簽章保存當時role/qualification/policy snapshot，後續角色異動不重寫歷史。

高風險操作按record scope核准，不給「核准全部」的永久旁路。重送同已核准命令可由service執行，但不能擴大原amount、audience或document version。

## 5. 金鑰、檔案與供應商

KMS/secret manager保存provider credentials及encryption key refs；簽章private key在核准HSM/供應商，不入LLM context或Git。key rotation可雙key驗舊事件一段核定期間，記key ID與撤銷時間。webhook驗raw bytes與timestamp/replay cache，大小/內容型態限流，拒任意JSON重序列化後驗簽。

上傳quarantine、掃毒、magic-byte檢查、大小限制、checksum，再轉受控object。下載按業務授權發短效URL或proxy，不能沿用customer upload提供的bucket key直接當已授權文件。外部來源URL allowlist、防SSRF（內網/metadata IP/redirect驗證）、timeout與大小限制。

## 6. 威脅與必測案例

| 威脅 | 防護與測試 |
|---|---|
| IDOR / tenant偽造 | 換customer/report UUID仍拒403/404；檔案URL不跨租戶 |
| 身分錯合併 | 僅verified provider subject可bind；email相同不自動合併 |
| webhook偽造/重放 | 壞簽章、舊timestamp、同event異hash拒；同event同hash去重 |
| 報告替換 | 同bundle簽章後改檔digest不符阻擋；簽章role已撤銷不新簽 |
| prompt injection | 外部報告文字只作資料；模型要求改權限/匯款被工具allowlist拒絕 |
| 重複退款/訊息 | idempotency+provider query+鎖餘額；unknown不換key盲重送 |
| 權限殘留 | 員工離職立即revocation、session失效與assignment轉移 |
| log/export洩漏 | redact敏感欄位；匯出scope與下載日誌、限時link |
| race/TOCTOU | 送出前權限重查；簽署/acceptance用版本與gate快照鎖 |
| 依賴/供應鏈 | 鎖定套件與CIaction SHA、secret掃描、SBOM、來源簽章與漏洞修補 |

## 7. 公司與委外邊界

公司掌管production帳號核准、資料、內容上架與備份檢查。既有營業計畫對委外廠商有客戶/KYC/投資履歷與交易資料不得接觸的控制承諾；非正式環境採合成資料，維修以錯誤代碼、脫敏log與可重現fixture完成。不能用一般break-glass流程把資料開給外包。

公司內部緊急存取另設ticket、owner核准、理由、scope、短時限、全程稽核及事後review；緊急存取不賦予分析師簽章或財務self-approval。

## 8. 驗收與部署證據

資料隔離要在正常DBserver、非superuser runtime role測試；SQL standalone解析或執行不能證明RLS生效。權限矩陣覆蓋允許/拒絕/他人資源/過期資格/同人製覆核/撤權競爭。正式釋出前公司核定IdP、signature/PSP providers、encryption/retention/secret政策與應變責任，文件建議不等於已配置完成。
