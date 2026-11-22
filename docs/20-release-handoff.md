# 20｜全範圍交付、驗收與移交清單

## 本平台工程完成

M01–M14全部後端、管理介面、worker、資料與權限完成；182個業務驗收按實作執行並有證據；cross-module E2E、failure/retry、對帳/獎金、簽章/發布與audit可操作；沒有拿mock stub充當真正本平台業務邏輯。具體case數若後續增加，以qa/test-catalog為準，不因數量固定拒新增必要案例。

## 外部連線與production readiness

Pantheon、LegendFlow及Agora是外部系統，不修改其repo。交付可包含本平台spec/mock/adapter完整而對方尚NOT_CONNECTED；此狀態應如實列在對接矩陣。取得正式介面/供應商sandbox/公司核定設定後，完成本平台端的連線和驗證，再判定正式啟用條件，不能聲稱已在對方環境部署。

## 公司移交

- 程式及schema版本、完整變更紀錄與十四Issue/PR索引。
- 開發/測試/正式環境配置清單，秘密經公司核准通道交接。
- 客戶先簽後審條款、問卷/AML/研究review/簽署方法等核定版本。
- 金額/稅務/收入/總帳/獎金policy，試算與正式模式清楚。
- 管理者、角色/資格、maker/checker與離職撤權測試。
- 例外queue、告警、deadletter、退款unknown與更正撤回操作演練。
- 文件/物件留存、legalhold、證據匯出與備份復原實測。
- 未完成外部接線清單，具體缺口、owner與本平台已完成部分。

## 不可用來宣告完成的替代物

只有文件、只有漂亮畫面、只有happy path、API只回固定JSON、RLS只在superuser測、退款只寫status、發送只寫outbound note、獎金用了未核定rate、某些模組留待下一期，都不構成完整系統交付。
