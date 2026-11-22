# 14｜非功能需求、容量、監控與服務目標

以下是可測的初始工程設計目標，供HerdR實作與壓測；不是已經達成的SLA或使用者已核定成本。正式負載、RPO/RTO和供應商額度由決策 OPS-01定案，未定案仍可依此做工程測試。

## 1. 初始負載模型

測試資料：10,000個合成客戶、每客戶最多5份並行服務、200,000則歷史交付、100,000筆財務/獎金entries、50名同時登入員工。測試流量：客戶查詢100RPS、員工操作20RPS、單次公告/建議5,000合格收件人、外部webhook重放10倍。這是容量假設，不是營收預測或實際客戶量。

## 2. 可量測目標

| NFR ID | 目標 | 邊界/量測 |
|---|---|---|
| NFR-01 | 內部一般query p95≤500ms、p99≤1.5s | 不含瀏覽器/供應商；記資料量與cache狀態 |
| NFR-02 | command受理p95≤1s | 外部長工作回202+job，不等待整批訊息完成 |
| NFR-03 | 簽章驗證commit到LegendFlow feed可查p95≤5s、p99≤15s | 同平台/本地adapter承諾；供應商故障獨立列 |
| NFR-04 | 5,000名收件人的dispatch eligibility決策≤30s | provider速率限制另外分批，不承諾手機實收30s |
| NFR-05 | 收件API重複10次不重複產業務效果 | idempotency/provider lookup/inbox證據 |
| NFR-06 | 10,000客戶、100,000收入項月結≤10min | 完整對帳/佣金計算結果可重現 |
| NFR-07 | 服務可用性工程目標99.9%/月 | 依監測SLO窗口；不表示正式合約保證 |
| NFR-08 | DB RPO≤5min；核心營運RTO≤60min | 必須實際restore＋provider reconcile演練 |
| NFR-09 | 敏感操作100%有audit linkage | 任何gap當控制缺失，不用抽樣掩蓋 |
| NFR-10 | 每個業務module具允許/拒絕/tenant隔離測試 | 非superuser DB模式驗RLS |

NFR-03量測起點是有效簽章核驗後，若人工review尚未完成不在計時範圍；停發/過期/撤回是業務blocked，須顯原因與停留時間，不視為靜默正常。

## 3. Metrics與告警

| 指標 | 建議告警條件 | Owner / 第一處置 |
|---|---|---|
| signature_to_feed_seconds | p95>5s連續5min | M07–09，查event/outbox/consumer lag |
| outbox_oldest_age_seconds | >30s持續2min（高優先發布） | platform，查lease/worker/DB |
| provider_unknown_transactions | 任何新refund/payment unknown | finance，查詢/對帳，不盲重送 |
| delivery_expired_retry_blocked | 次數突增 | delivery，檢查排程延誤/有效期 |
| entitlement_recheck_denied | 相對snapshot異常增加 | M02/M09，核對撤權與快取 |
| reconciliation_unmatched_amount | 超已核定門檻或超1個作業日 | finance，差異queue |
| commission_policy_not_approved | 正式batch任何一次 | commission，阻止付款並處理policy |
| audit_link_missing | >0 | internal control，保留現場並修復來源 |
| aml_provider_unavailable | 超5min或名單過期 | AML專責；一般staff僅顯等待 |
| support_sla_breaches | 重大案件任何一次 | service supervisor，升級處理 |
| clock_offset_seconds | >核定容忍值 | infra，阻擋依賴可信時點動作 |
| llm_cost/task_failure | 超budget/連續失敗 | AI owner，降級到模板/人工queue |

具體閾值是可配置初始值；高風險項採事件立即通知、低風險採聚合避免告警疲勞。告警不含原始PII、AML詳細或完整客戶訊息。

## 4. Log與trace

結構化log：service/module、operation、status/error_code、tenant pseudonym、correlation/causation、aggregate/version、attempt與latency。不要log token、原表單、卡號、身份證或完整AML結果。trace貫穿callback→inbox→domain→outbox→worker→provider，外部request ID以reference保存。成功與失敗抽樣策略不可讓財務/核決audit遺失。

## 5. 壓測與容量回報

固定seed、scenario、concurrency、warmup、duration、image/schema版本；包含cache miss、大附件、provider慢/429/timeout、worker重啟、DBfailover、同customer競爭與爆量重放。輸出p50/p95/p99、error率、CPU/memory/DB鎖/queue age、費用與瓶頸。只有單次本機快速測試不能宣稱NFR達成。

## 6. 可用性設計

API無狀態多副本，worker lease可恢復，Postgres備援與PITR、物件版本/備份、queue持久化。供應商斷線用circuit breaker與可見待辦；重要流量隔離，LLM耗盡預算不能拖垮權限檢查或退款對帳。故障降級不越過法律/權限gate，不以「提高可用性」發布未簽內容。
