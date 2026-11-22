# HerdR 十四模組同時開發任務包

本交付要求 **十四個模組全部完成且盡可能平行開發**。排程十四個邏輯工作流時，各自使用相同契約基線、獨立branch/worktree與明確檔案歸屬；API與事件供應方未完成時用mock繼續，不能把依賴關係解讀為必須逐一完成。

本目錄是可供HerdR消化的Markdown工作規格，不冒充未經驗證的HerdR原生設定格式。啟動方式依實際HerdR環境；文件備妥不等於已啟動14個agent。

| 模組 | 範圍 | 任務包 | 分支 | 開工依賴 |
| --- | --- | --- | --- | --- |
| M01 | 客戶、身分與申辦核心 | [M01-customer_identity_application.md](M01-customer_identity_application.md) | module/m01-customer_identity_application | 無；contracts/mocks就緒即可 |
| M02 | 線上契約、公司接受與服務權限 | [M02-contracts_entitlements.md](M02-contracts_entitlements.md) | module/m02-contracts_entitlements | 無；contracts/mocks就緒即可 |
| M03 | 線上 KYC、客戶資料與適合度 | [M03-kyc_suitability.md](M03-kyc_suitability.md) | module/m03-kyc_suitability | 無；contracts/mocks就緒即可 |
| M04 | AML/CFT 查核、風險與受限案件 | [M04-aml_cft.md](M04-aml_cft.md) | module/m04-aml_cft | 無；contracts/mocks就緒即可 |
| M05 | Pantheon 候選研究接收與證據驗證 | [M05-pantheon_intake.md](M05-pantheon_intake.md) | module/m05-pantheon_intake | 無；contracts/mocks就緒即可 |
| M06 | 研究覆核工作台與 Agora／Servant 協作 | [M06-agora_review.md](M06-agora_review.md) | module/m06-agora_review | 無；contracts/mocks就緒即可 |
| M07 | 正式研究報告、覆核與分析師簽章 | [M07-report_signature.md](M07-report_signature.md) | module/m07-report_signature | 無；contracts/mocks就緒即可 |
| M08 | 建議序列、簽章驅動發布與更正 | [M08-publication.md](M08-publication.md) | module/m08-publication | 無；contracts/mocks就緒即可 |
| M09 | LegendFlow、會員服務與即時通路投遞 | [M09-legendflow_delivery.md](M09-legendflow_delivery.md) | module/m09-legendflow_delivery | 無；contracts/mocks就緒即可 |
| M10 | 客戶漏斗、業務維護與轉換歸因 | [M10-crm.md](M10-crm.md) | module/m10-crm | 無；contracts/mocks就緒即可 |
| M11 | 維護歸屬、離職移交與公司 Pool | [M11-ownership_pool.md](M11-ownership_pool.md) | module/m11-ownership_pool | 無；contracts/mocks就緒即可 |
| M12 | 獎金政策、計算、調整與結算 | [M12-commissions.md](M12-commissions.md) | module/m12-commissions | 無；contracts/mocks就緒即可 |
| M13 | 線上線下客服、申訴與跨部門案件 | [M13-service_cases.md](M13-service_cases.md) | module/m13-service_cases | 無；contracts/mocks就緒即可 |
| M14 | 帳務、對帳、退款與內控證據 | [M14-finance_control.md](M14-finance_control.md) | module/m14-finance_control | 無；contracts/mocks就緒即可 |

## 協調者如何分派

1. 讀[根AGENTS](../../AGENTS.md)與[模組manifest](../../project/module-manifest.json)，固定同一base commit。
2. 同時交出上述14包；每包只寫owned paths，共用schema、migrations、router、lockfiles由steward整合。
3. 每個agent先跑contracts/mocks，接著完成本域API、UI、workers與測試；不為其他模組未完成而閒置。
4. 持續整合producer／consumer契約，完成時替換mock為實際adapter並跑sandbox驗證。
5. 以[168個模組UAT與14個E2E](../../qa/uat.md)及[追蹤矩陣](../../qa/traceability.csv)驗收；十四模組及整體旅程全過才交付。

## 不可混淆的完成狀態

文件與schema備妥、mock通過、模組程式完成、供應商sandbox打通、全範圍UAT通過、正式上線是不同狀態。沒有對應執行證據時維持NOT_RUN或not_started，不打勾假裝已完成。

## 共通決策邊界

客戶先簽後線上KYC，由公司接受後才依條件開權限；Pantheon/Agora/LegendFlow為外部系統。報告與訊息包先覆核再簽，驗證簽章自動發布。七日Pool規則不是未聯絡七天；全部付費服務中斷的解讀仍為待核定設定，離職立即移公司。獎金100/50/25、介入續約30/70、實際未稅服務收入扣退款為已確認；基準率與衰減交互算法未核定不能live。
