# M04 AML/CFT 模組測試

執行：

```bash
python3 -m pytest tests/modules/aml_cft -q
```

- `test_uat.py`：UAT-M04-001 至 012，逐案對應 `docs/modules/M04-aml_cft.md` 第 14 節的前提／操作／預期。
- `test_contract_worker_http.py`：8 個 operationId 的 route／controller ETag／Idempotency-Replayed／Problem 行為；`aml.case_changed`／`business_clearance.changed` 對 `events/schemas` 驗證；回應對 `api/openapi.yaml` 1.0 schema 驗證；`AmlEventConsumer` 的去重／hash／sequence gap／跨租戶；`OutboxPublisher` 的 lease／bounded retry／不重複發布；production 組裝拒絕 fake adapter。
- `test_security_automation.py`：跨租戶隔離、customer 自助聲明不可冒用他人 application、webhook 僅限 service+`aml_adapter`、`CaseSummaryAutomationService` 不具任何變更案件/核定能力且 production 拒絕 fake assistant、受限證據不存在時的驗證錯誤。
- `test_ui_contract.py`：`apps/admin/src/features/aml_cft` 匯出與八種 UI 狀態標籤、maker-checker 與 server 決定 `allowed_actions` 的文字標記、一般平台元件不得出現受限欄位名稱、鍵盤焦點與非顏色狀態文字。

本模組目前僅驗證 Python 應用層（`pytest`）與 UI 原始碼內容斷言；前端 `tsc`/`vite build` 需要 `apps/admin` 的 `node_modules`，本次 worktree 未安裝（沒有可用的 `pnpm`／lockfile），故 `apps/admin` 的 `npm run typecheck` 為 `NOT_RUN`，留待整合者在有安裝 workspace 依賴的環境執行。`python scripts/validate_contracts.py` 已在本次驗收中執行，見 handoff 證據。
