# M13 共用契約相容性註記

本模組沒有向 `api/openapi.yaml` 或 `events/registry.json` 提出新增契約；14 個
M13 operationId 與 4 個 producer 事件（`support.case_opened` /
`support.case_escalated` / `support.case_resolved` /
`support.response_status_changed`）已足以實作全部 REQ-M13-001～012。

## 待 integration/QA steward 核對的落差

`qa/test-catalog.json` 的 `UAT-M13-006` 項目將 `request_refund` 列為
`operation_ids` 之一。該 operationId **確實存在**於 `api/openapi.yaml`
（`POST /finance/refunds`，`x-module: M14`，`x-allowed-roles` 含
`service_agent`），所以它不是一個需要契約變更才能存在的新端點；差異在於
**它屬於 M14 的 owned_paths，不屬於本模組**。`docs/modules/M13-service_cases.md`
§8 的 API 契約表只列本模組自己實作的 operationId，因此沒有出現
`request_refund` 並不代表該端點不存在，只代表它不是由
`apps/api/src/modules/service_cases/` 提供實作。

依任務包「不能在本模組私增列舉」與 M13 write_scopes 不含
`apps/api/src/modules/finance_control/`（M14）的邊界，本模組**沒有**也**不能**
自行實作或呼叫 `request_refund`；目前以既有、確定在本模組 owned_paths 內的契約
滿足相同業務意圖：

- `escalate_service_case(target_queue="finance")` 代表客服提出退款/財會處理請求。
- 實際核准與出金在 M14（`finance.refund`，已存在於
  `database/migrations/0001_domains.sql`，含 `prepared_by <> approved_by` 的
  SoD 檢查），包括 `request_refund` 本身的實作，不在 M13 範圍。
- `finance.refund_confirmed` 事件回填 `support.case_file.linked_refund_id`
  （consumer 投影，見 `workers/tasks/service_cases/consumer.py`），客服端完全
  沒有對應的 approve/execute 方法，也沒有呼叫 M14 `request_refund` 的用戶端程式碼。

這是一個 **M14 cross-module port 邊界**問題，而非契約缺失：`request_refund`
對 M13 而言是 `NOT_CONNECTED`（契約已定義、角色允許、但本模組未實作呼叫端，
且 M14 尚未在本次候選中交付）。建議 QA/integration steward 保留
`UAT-M13-006.operation_ids` 中的 `request_refund`，但將其驗收歸屬改記為
依賴 M14 候選交付與一個 M13→M14 的 port 實作，而不是要求移除該
operationId；`escalate_service_case` 仍可作為 M13 自身可驗收的前置步驟
一併保留於同一 UAT。
