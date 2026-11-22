# 共用管理介面元件契約

所有feature使用相同AppShell、WorkQueue、CustomerSummary、StatusGate、EvidenceDrawer、Timeline、VersionDiff、DecisionForm、MoneyBreakdown、AsyncJobStatus及PermissionBoundary。

| 元件 | 必要props/資料 | 狀態/責任 |
|---|---|---|
| WorkQueue | rows、cursor、filters、as_of、owner、next_action、due_at | loading/empty/stale/error分開；可鍵盤選擇 |
| StatusGate | gate_code、status、reason、valid_until、allowed_actions | 多個gate不可合成已開通布林值 |
| EvidenceDrawer | object_ref/version/hash、classification、authorized download | 下載前授權；無權限不預載 |
| VersionDiff | base/current、changes、stale_reviews | 不自動套用過期approval |
| DecisionForm | aggregate_version、idempotency_key、decision、reason、step_up | 驗證/競爭/送出pending/去重結果 |
| MoneyBreakdown | currency、decimal amounts、policy_version、source refs | 展示gross/tax/net/fee/adjustments，計算在server |
| AsyncJobStatus | job_id、state、last_error_code、retry_at、provider_ref | unknown≠failed，禁止盲重送付款/訊息 |
| Timeline | occurred/recorded times、actor、source、correlation | 跨渠道同案、immutable事件、非完整PII |
| PermissionBoundary | server allowed_actions與resource_scope | UX提示，不替代API/DB授權 |

元件測試須含窄螢幕、keyboard、screenreader、200%縮放、長中文名稱/金額/多時區和權限撤銷。實際前端build由UI steward實作，這份文件不假稱已有可用prototype。
