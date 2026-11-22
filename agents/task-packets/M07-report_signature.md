# M07 HerdR 任務包 · 正式研究報告、覆核與分析師簽章

## 任務契約

| 欄位 | 值 |
| --- | --- |
| 模組 | M07 / `report_signature` |
| Issue | [#7](https://github.com/ajoe734/longhao-advisory-ops-platform/issues/7) |
| 分支 | `module/m07-report_signature` |
| Worktree | `../longhao-worktrees/m07` |
| 開工依賴 | 無其他模組完成依賴；依同一契約基線與mock立即開發 |
| 整體交付範圍 | 全部十四模組；本模組所有REQ與UAT均必須完成 |
| 外部接線狀態 | 本次以本平台adapter＋mock完成範圍；真實接線另列NOT_CONNECTED，不改外部系統 |

把外部研究轉成龍好正式投顧報告版本，完整保存覆核與簽章證據。簽章精確綁定報告、建議、訊息版本及發布範圍；有效簽章確認後自動觸發M08發布。

## 外部系統的硬邊界

Pantheon、Agora／Servant、LegendFlow均為外部系統。本次僅建立串接規格、測試替身，以及最多在本repository實作本平台adapter；**禁止修改外部專案、向外部專案提交PR、執行migration或部署**。外部尚無端點時，使用本平台契約mock完成開發與模擬驗收，將真實連線標記NOT_CONNECTED；不得要求agent到外部補程式，也不得以mock成功聲稱已打通。真實接線若日後啟用，另以對方已提供的接口做授權測試；不改變本次十四模組完整開發範圍。

## 1. 必讀與輸入

依序讀[根AGENTS](../../AGENTS.md)、[總體SA](../../docs/02-SA.md)、[總體SD](../../docs/03-SD.md)、[本模組SA/SD](../../docs/modules/M07-report_signature.md)、[OpenAPI](../../api/openapi.yaml)、[事件registry](../../events/registry.json)、[資料字典](../../database/data-dictionary.md)及[UAT catalog](../../qa/test-catalog.json)。使用[模組manifest](../../project/module-manifest.json)中的分支與檔案歸屬。

HerdR應把此任務與其餘13包同時排程。文件不假設未驗證的HerdR CLI參數或config格式，也不宣稱本次已啟動14個實作agent。邏輯任務全部可開始；執行機器的worker併發上限由實際HerdR環境設定，整合責任不因併發上限而被刪除。

## 2. 檔案寫入責任與衝突隔離

- `apps/api/src/modules/report_signature/`
- `apps/admin/src/features/report_signature/`
- `workers/tasks/report_signature/`
- `tests/modules/report_signature/`
- `database/proposals/M07/`

API controller／application service／domain rules／repository port放本模組目錄；UI放本模組feature；worker只放本模組task；資料庫變更先送`database/proposals/M07/`。共同`api/`、`events/`、`database/migrations/`、router總表、lockfiles、根CI與共用UI元件交由integration steward核對合併，不由14agent各自修改。可在本模組測試建stub，不能直接寫他模組表以假裝串接完成。

若協調者尚未建立worktree，可在乾淨repo執行下列Git命令；已存在時使用既有worktree，不重建、不force reset、不覆蓋其他人的未提交工作：

```bash
git worktree add ../longhao-worktrees/m07 -b module/m07-report_signature origin/main
```

## 3. 不等待其他模組的開始方式

1. 讀本模組OpenAPI operations與其request/response fixture；固定client型別，先完成UI狀態與domain規則。
2. 對所有外部／跨域依賴定義port，使用[mock server](../../mocks/server.py)、fixture及事件example替代尚未合併的provider。
3. 為success、denied、stale_version、timeout、duplicate與out_of_order做本模組測試；使用可控clock與合成actor，不依實際客戶／憑證。
4. Provider模組合併後只替換adapter；重新跑雙向contract和本任務參與的E2E。Mock通過不能列為外部真串接通過。

## 4. 本模組完整實作工作

- [ ] WORK-M07-01：組裝正式版本。以M05/M06來源建立report，記錄判斷、依據、風險、利益衝突揭露及有效期限；對應M08建議與各通路內容；輸出固定canonical bytes及內容hash。
- [ ] WORK-M07-02：完成覆核與凍結。專業與依政策必要法遵覆核均完成；覆核對象為報告、建議、渠道文字及audience policy完整manifest；以CAS凍結，驗證沒有後來來源使review過時。
- [ ] WORK-M07-03：簽署與自動交付。授權分析師明確確認frozen package並送簽；provider回呼驗證簽者、資格、檔案與時間戳；同一資料庫交易記signature verified及outbox事件；M08自動發布。撤銷或後續更正只追加新紀錄。

- [ ] 建立本模組domain錯誤與狀態轉移，先驗actor/tenant/SoD，再驗版本與業務條件。
- [ ] 寫本域repository與transaction，狀態＋audit＋outbox原子提交；inbox去重並檢查payload hash。
- [ ] 實作第5節全部API，不交只有200 response的stub；202提供可查receipt／job。
- [ ] 實作SA/SD第10節畫面，含loading/empty/denied/stale/conflict/processing/error與可及性；allowed_actions由server決定。
- [ ] Worker具lease、bounded retry、timeout、deadletter、狀態查詢與安全重驅；不對unknown外部結果盲重送。
- [ ] 接入核准的LLM庶務capabilities，保留來源與model/prompt/ruleset版本；禁止擅自簽章、清除AML、出金或授權。
- [ ] 建立metrics、alert與runbook；示範一次本域失敗恢復與資料對帳。
- [ ] 更新需求→API→資料→測試追蹤，回報完整修改與證據，交integration steward合併。

## 5. API 實作清單

- [ ] `create_report`：將候選研究建立為正式投顧報告工作項目；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `create_report_version`：建立不可覆寫的報告／訊息／受眾規則版本；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `get_report_version`：取得簽章包與研究證據鏈；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `record_report_review`：專業或法遵審查綁定精確版次及雜湊；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `freeze_report_version`：凍結報告、建議、各渠道文本與受眾規則；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `request_analyst_signature`：授權分析師簽署已完成所有審查的版本；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `receive_analyst_signature`：驗證最終簽章；成功事件自動觸發發布；實作request/response schema、權限、版本／去重及明確錯誤。
- [ ] `revoke_signature`：新增撤銷證據，保留原簽章與下游撤回事件；實作request/response schema、權限、版本／去重及明確錯誤。

## 6. 事件對接責任

| 事件 | 方向 | Producer | Consumers | 分級 | Schema版本 |
| --- | --- | --- | --- | --- | --- |
| report.version_frozen | 發出／接收 | M07 | M07 | research_confidential | 1.0.0 |
| report.signature_verified | 發出 | M07 | M08 | research_confidential | 1.0.0 |
| report.signature_revoked | 發出 | M07 | M08,M09 | research_confidential | 1.0.0 |
| review.revision_received | ／接收 | M06 | M07 | research_confidential | 1.0.0 |
| research.candidate_superseded | ／接收 | M05 | M06,M07 | research_confidential | 1.0.0 |

上述事件的schema和example均在events目錄。Producer測試確認只在成功提交後發出、同一命令重送不重複副作用；consumer測試確認重送、亂序、租戶、schema不符、撤銷／過期的處理。Consumer不能以收到事件當已獲授權；需要即時門檻者重新查權威狀態。

## 7. 資料與例外實作重點

| 資料表 | 責任 | 用途 | 唯一性／識別 | 資料分級 | 留存分類 |
| --- | --- | --- | --- | --- | --- |
| report.report | 本模組權威寫入 | 正式投顧研究報告的穩定識別。 | report_number | internal | report |
| report.report_version | 本模組權威寫入 | 不可變報告正文版本及簽前核定 bundle；狀態投影可前進。 | report_id,report_version_no | internal | report |
| report.review_decision | 本模組權威寫入 | 逐版本覆核，綁相同內容與 bundle；修訂後不能沿用。 | tenant_id,id | internal | report |
| report.signature_request | 本模組權威寫入 | 簽章 provider 呼叫狀態；逾時需查詢，不可盲目重簽。 | provider,provider_transaction_id | internal | report |
| report.signature | 本模組權威寫入 | 已驗證分析師簽章證據；永不改內容。撤銷用 revocation。 | request_id；report_version_id | internal | report |
| report.revocation | 本模組權威寫入 | 撤銷或重驗失敗保留原簽章證據。 | tenant_id,id | internal | report |

- provider簽章成功但callback遺失：以request id查證，不要求分析師盲目重簽。
- content_hash不是signed_file_hash；簽章容器加入憑證後bytes改變應另存hash，不能誤判原文被改。
- 簽署者資格在request與verification時重驗；實際生效時間與撤銷狀態需保存。
- 影像章與密碼點按是否符合公司核定簽署控制須由實際provider評估；文件不把某CA等級當已驗證法律結論。

## 8. 必須交付的驗收證據

- [ ] `UAT-M07-001`：缺必要覆核不能送簽。通過條件：422 missing_review；無有效signature或publication事件
- [ ] `UAT-M07-002`：凍結包包含報告與訊息範圍。通過條件：生成manifest綁report、recommendation、channeltexts、audience policy與expiry hash
- [ ] `UAT-M07-003`：無有效資格者不能簽署。通過條件：403/qualification_invalid；沒有provider有效request
- [ ] `UAT-M07-004`：簽章回呼只在完整驗證後觸發事件。通過條件：signature verified與唯一report.signature_verified outbox在同transaction；之後M08自動發布
- [ ] `UAT-M07-005`：簽後修改建立新版本。通過條件：v1正文與frozen manifest不可覆寫；產生v2 draft並重新覆核／簽署，v1證據保留，狀態只依合法事件演進
- [ ] `UAT-M07-006`：內容與簽章檔hash分開。通過條件：content_hash=A來源內容，signed_file_hash=B；能驗證封裝關係，不要求A等於B
- [ ] `UAT-M07-007`：資料過時阻擋簽章。通過條件：按policy拒絕或中止request；不能沿用過期review自動發布
- [ ] `UAT-M07-008`：provider重送不重複發布。通過條件：一signature、一verified event；後次回相同receipt
- [ ] `UAT-M07-009`：簽章payload替換遭拒。通過條件：invalid且告警；原frozen package保留；不發publication
- [ ] `UAT-M07-010`：撤銷保留歷史並通知發布服務。通過條件：append revocation、發撤銷事件；不刪signature；後續投遞停止並走更正
- [ ] `UAT-M07-011`：簽署者知悉簽後自動發布。通過條件：畫面顯示自動發布與範圍；不在簽後另加人工Publish gate
- [ ] `UAT-M07-012`：多次對同版覆核保留完整歷史。通過條件：舊review仍指v1；v2有新review；每位reviewer與時點可追溯

跨域旅程見[完整UAT](../../qa/uat.md)；每筆結果記actor、tenant、policy/schema、request/response、時間、DB state、audit與provider receipts。只列passed而沒有執行命令／commit／證據不算完成。

## 9. 驗證指令與邊界

目前文件包可直接驗證contract：

```bash
python scripts/validate_contracts.py
```

本模組完成後必須提供可執行的模組測試命令，例如已建立Python測試時使用：

```bash
python -m pytest tests/modules/report_signature -q
```

第二個命令是實作agent必須交付的驗證入口，不代表此文件包已有應用程式或該測試已通過。SQL migration驗證按[資料庫README](../../database/README.md)，前端lint/typecheck/UI測試按實際專案工具設定；不能捏造尚不存在的pnpm script。必要測試覆蓋上述業務不變量、權限與失敗恢復，不為了數量複製實作邏輯當測試。

## 10. 合併與完成回報

本模組可獨立開發、可持續合併符合契約的部分，但issue維持open直到全部範圍完成。所有14模組與完整跨域UAT達標才宣告整體完成，不把某條旅程先可用當成全案完成。

提交回報必須含：

- changed files與本模組所有REQ完成狀態；未完成者不得省略。
- commit／PR、API及事件契約版本、migration proposal與版本衝突處理。
- 真正執行的驗證命令、結果、UAT evidence refs、失敗及NOT_RUN項目。
- 外部adapter哪些mock、哪些sandbox驗證；未核定政策是否確實被生產gate阻擋。
- 整合風險、需協調者合併的共用契約變更與操作runbook位置。
