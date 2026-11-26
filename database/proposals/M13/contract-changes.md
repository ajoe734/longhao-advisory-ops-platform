# M13 共用契約相容性註記

本模組沒有向 `api/openapi.yaml` 或 `events/registry.json` 提出新增契約；14 個
M13 operationId 與 4 個 producer 事件（`support.case_opened` /
`support.case_escalated` / `support.case_resolved` /
`support.response_status_changed`）已足以實作全部 REQ-M13-001～012。

## 待 integration/QA steward 核對的落差

`qa/test-catalog.json` 的 `UAT-M13-006` 項目將 `request_refund` 列為
`operation_ids` 之一，但該 operationId 不存在於 `api/openapi.yaml` 的 M13 區段，
也未在 `docs/modules/M13-service_cases.md` §8 的 API 契約表中出現。依該文件自身
規則（「若契約真的不足，先送契約變更，不能在本模組私增列舉」），本模組**沒有**
自行新增此端點，而是用既有契約滿足相同業務意圖：

- `escalate_service_case(target_queue="finance")` 代表客服提出退款/財會處理請求。
- 實際核准與出金在 M14（`finance.refund`，已存在於
  `database/migrations/0001_domains.sql`，含 `prepared_by <> approved_by` 的
  SoD 檢查），不在 M13 範圍。
- `finance.refund_confirmed` 事件回填 `support.case_file.linked_refund_id`
  （consumer 投影，見 `workers/tasks/service_cases/consumer.py`），客服端完全
  沒有對應的 approve/execute 方法。

建議 QA/integration steward 更新 `qa/test-catalog.json` 的 `UAT-M13-006.operation_ids`
為 `["escalate_service_case"]`，移除不存在的 `request_refund`；或若產品真的需要
一個獨立的 `request_refund` operationId，應先走 OpenAPI 契約變更流程再實作，
不應由本模組單方新增路由。
