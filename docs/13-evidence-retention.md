# 13｜證據鏈、文件保存與稽核匯出

## 1. 必須能回答的稽核問題

任選一則已傳給客戶的建議，系統能查出：實際通路與文字、對應recommendation/report版本、合理分析依據與Pantheon來源、覆核人與有效資格、簽章與驗證、發布/發送各時點、當時客戶契約權限、實際送達證據、後續更正或撤回。不能只有一個「已簽章」布林值或一份事後補上的PDF。

任選一筆獎金，能查出收入和履約期間、退款調整、原始來源cohort、業務介入證據、歸屬時點、policy、計算係數、核決與付款回執。任選一個客戶，能查出先簽文件、各審查版本、公司承諾、契約生效、開通/限制與交付歷史。

## 2. 證據的三個層次

| 層 | 保存內容 | 完整性 |
|---|---|---|
| 業務紀錄 | aggregate ID/version、狀態與決策 | CAS/FK/CHECK、角色與transaction |
| 不可變物件 | 已簽文件、原始來源快照、通路render、provider回執 | SHA256、storage version、KMS、retention lock |
| 稽核事件 | actor/role snapshot、action、reason、前後hash、時間、correlation | append-only、外部保存/簽定期manifest、受控查閱 |

單純資料庫append-only trigger不能防止高權DB管理員修改，正式環境需公司控管、獨立寫入權的log/物件保存與定期hash manifest比對。存取紀錄也必須留存，含下載/匯出敏感資料。

## 3. Hash、時間與版本

原文bytes的content hash與簽後檔案hash分開。JSON需定義canonical encoding；文件用實際可下載bytes，預覽HTML/文字摘錄的hash不可冒充原PDF hash。簽章bundle manifest列每件object版本、hash、用途與審查引用；任何內容替換都成新bundle。

保存occurred_at、source_available_at、received_at、decided_at、signed_at、verified_at、published_at、dispatched_at與provider receipt時間，視實際物件適用。所有時間可指出來源（server/provider/客戶申報），clock同步告警；不以來源Persona的交易時間回填報告發布。

## 4. 保存schedule

| 類別 | 啟算事件 | 最低設定／狀態 | 備註 |
|---|---|---|---|
| 正式投顧契約 | 權利義務關係消滅 | 管理規則第11條：5年 | 有爭議/hold延長 |
| 投資分析報告副本與紀錄 | 提供之日 | 管理規則第11條：5年 | 多次提供採可涵蓋最後應保存義務的日期 |
| AML交易必要紀錄 | 適用交易事件 | AML辦法第12條：至少5年 | 較長法令優先 |
| 身份確認/業務往來文件 | 業務關係或臨時交易結束 | AML辦法第12條：至少5年 | 不是上傳日起一律五年 |
| 財務憑證/帳簿/薪酬 | 各類核定起算 | 財會/法遵另核定具體年期 | 未核定前不啟自動刪除 |
| 客服/錄音/廣告/系統log | 各適用事件 | 依分類與適用規範核定 | 不把研究5年套成全部唯一標準 |
| 草稿/AI暫存/非客戶leads | 目的完成或到期 | 隱私最小化policy核定 | 不因便利無限保存 |

法源與查證日見 `17-sources-compliance.md`。schedule是版本化設定，含min_period、trigger、legal_basis、owner、approved_by與effective_at；延長可做，縮短不可追溯破壞既有義務。legalhold覆蓋一般刪除。

## 5. 刪除與資料主體請求

接到更正/刪除/存取請求先驗身份、界定資料與法律保存需求。可更正的客戶資料追加版本；已簽或已發布文件保留原證據與更正鏈。受保存義務/hold的部分限制使用與呈現，不能因客戶按刪帳而刪掉必須保存的證據。清理job先dry-run、核准、逐object結果與可驗manifest，再依policy進不可復原刪除；不得以DB cascade delete清除全部客戶歷史。

## 6. 稽核包格式

Export request：requester、purpose、scope（customer/report/time/transaction）、classification、approver、expires_at。產物：manifest.json、關聯CSV/JSON、允許的原物件、hash列表與validation-report。manifest包含schema_version、generated_at、source snapshot、排除/遮罩說明、record counts和每檔sha256。

員工可從單一report/message ID起查；系統決定最小鏈路，不需手動從五個系統截圖。普通稽核包不帶AML全文；受限AML包另scope/權限/審核。匯出到公司私有儲存，下載有期限；對外提供由授權程序處理，不在public Git。

## 7. 健康檢查與驗收

每日核對已簽report是否有物件+signature+review、已發布是否有signature event/受眾、已送是否有即時授權決策與message version、已計獎是否有收入來源與policy。缺link進例外不得自動補造歷史。每季抽樣還原證據包並校hash；復原演練驗資料、物件、金鑰引用和outbox鏈一起可用。
