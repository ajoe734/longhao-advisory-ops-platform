# 龍好智慧投顧營運管理平台

全範圍開發規格與十四模組平行交付基線。客戶端由 LegendFlow 提供；Pantheon 產生研究候選，Agora／Servant 協助分析師修訂；本平台負責客戶與契約、審查與權限、研究簽章發布、CRM、獎金、客服、帳務及內控。

**交付目標：十四個模組全部完成並整合驗收。十四個工作流從同一份契約基線同時開工，以 port、schema、fixtures 與 mock 解耦。沒有縮減範圍的 MVP，也沒有將財會、AML/CFT、稽核或客服延後。**

本倉庫目前交付開發規格、資料遷移、介面契約、模擬服務、驗收與協作工具；文件存在不代表正式應用已實作、外部供應商已串通或十四個 HerdR agent 已啟動。正式進度以 Issue、PR、測試與環境證據認定。

## 從哪裡開始

1. [文件總目錄](docs/00-document-index.md)：每份文件的用途、權威範圍與交付檢查。
2. [平行開發總計畫](agents/PARALLEL_DEVELOPMENT.md)：十四工作流、共用檔案責任、依賴替身與全範圍驗收。
3. [HerdR 操作與分派](agents/HERDR_RUNBOOK.md)、[協調者提示詞](agents/COORDINATOR_PROMPT.md)、[模組任務包](agents/task-packets/README.md)。
4. [SA](docs/02-SA.md)、[SD](docs/03-SD.md)、[模組規格目錄](docs/modules/README.md)。
5. [API](api/openapi.yaml)、[API／事件規則](docs/09-api-events.md)、[資料庫](database/README.md)、[資料模型](docs/08-data-model.md)。
6. [UI/UX](docs/10-ui-ux.md)、[權限與職責分離](docs/11-security-rbac.md)、[驗收](qa/uat.md)。

## 十四個同時開發的模組

| ID | 模組 | GitHub 工作項目 |
|---|---|---|
| M01 | 客戶、身分與申請主檔 | [#1](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/1) |
| M02 | 客戶先簽、公司承諾、契約與服務權限 | [#2](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/2) |
| M03 | 線上 KYC 與適合度 | [#3](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/3) |
| M04 | AML/CFT 與限制案件 | [#4](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/4) |
| M05 | Pantheon 研究候選接收 | [#5](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/5) |
| M06 | Agora／Servant 審閱協作 | [#6](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/6) |
| M07 | 正式研究報告、覆核與分析師簽章 | [#7](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/7) |
| M08 | 簽章觸發發布與建議生命週期 | [#8](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/8) |
| M09 | LegendFlow、簡訊、即時訊息交付 | [#9](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/9) |
| M10 | CRM 漏斗、續訂升級與業務工作清單 | [#10](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/10) |
| M11 | 維護歸屬、交接與公司 Pool | [#11](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/11) |
| M12 | 行銷／業務獎金與結算 | [#12](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/12) |
| M13 | 線上線下客服、客訴與跨部門案件 | [#13](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/13) |
| M14 | 收款、對帳、收入、退款、財會與內控證據 | [#14](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/14) |

## 不可改寫的業務方向

- CMS 是客戶管理。服務條件作為契約、適合度與權限設定；不是另一套內容 CMS 或獨立方案管理產品。
- 線上客戶先簽申請／契約文件，後做 KYC、適合度與 AML/CFT；公司核准承諾、契約生效和權限開通分開記錄。
- Persona 可以自主發起研究；正式建議由分析師覆核、對確定版本簽章。簽章驗證通過後系統即自動發布，不增加第二次人工發布。
- 進出場與更新訊息均連到當時有效、已簽的研究依據。客戶不必先閱讀報告才使用通知。
- 網路來源、自助續訂、維護人、機會擁有人、實際促成業務與獎金收受人分開記錄；指派名單本身不構成業務獎金。
- 行銷係數 100%／50%／符合連續自動續訂的第三年起 25%；業務介入續訂獎金池 30/70；基礎為實際未稅服務收入扣除可歸屬退款。總獎金率及係數與分配的組合解讀尚需制度定案，不填造已核准數值。
- 「客戶斷掉一週」不等於每週必須聯絡。本基線將「全部付費服務中斷連續七天」列為待確認的實作解讀；離職即回公司 Pool。詳見決策紀錄與 CRM 規格。

## 開發驗證

```bash
python -m pip install -r requirements-dev.txt
python scripts/validate_docs.py
python scripts/validate_contracts.py
python scripts/validate_sql.py
python scripts/prepare_worktrees.py --base main
```

最後一個命令只顯示十四份 worktree 計畫；加 `--apply` 才建立。資料庫實際執行測試、mock 啟動方式與各模組測試依對應 README。上述規格檢查不能取代尚待實作的業務測試。

## 公開倉庫界線

僅存放公開安全的規格、合成資料與程式。原始營運計畫、核准函、客戶資料、KYC 附件、AML 案件、金鑰、憑證、帳密及正式財務檔不放入 Git。控制文件以代號、版本、頁次列於[來源與法遵對照](docs/17-sources-compliance.md)。
