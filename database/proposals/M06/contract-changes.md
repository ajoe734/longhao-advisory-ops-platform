# M06 共用契約相容新增提案

此檔不直接修改 `api/openapi.yaml` 或 `events/registry.json`，由 integration steward 合併：

1. `Revision` 可選新增 `significant_diff`（boolean）與 `diff_summary`（`string[]`，每則 ≤400 字）：目前
   `review.revision_result` 的差異只透過內部 `get_review_case_workspace()` UI 投影回傳，不在正式 `Revision`
   schema 內。對外契約要能驅動「版本比較」畫面，需要把這兩欄位加入 `request_servant_revision`/
   `receive_servant_revision`/`get_revision_request` 的回應 schema。
2. 註冊一個新的 `assign_review_case` 相容擴充或獨立命令，讓 `research_supervisor` 能設定
   `ReviewCase.professional_reviewer_id`（第二位 maker-checker 複核人員）與
   `compliance_review_required`。現有 `AssignmentRequest` 只有 `assignee_id`／`reason_code`／`effective_at`；
   在此契約核准前，這兩個欄位維持安全預設（`null`／`false`），模組不自行發明指派規則。
3. `review.revision_request` 新增 `retry_of_revision_id`：同一 conversation 下重送失敗修訂時，服務會自動把新
   `Revision.id` 連到上一個 `failed` revision；此欄位目前只在資料層／內部稽核可見，若要對外暴露於 `Revision`
   需另行走 API 契約流程。
4. `review.conversation.scoped_context_ref`（純文字）無法表達 OpenAPI `Conversation.scoped_artifact_ref` 要求的
   `{system, external_id, kind}` 結構；提案在 `001_agora_review.sql` 新增 `scoped_artifact_ref jsonb`
   並以既有 `provider` + `scoped_context_ref` 回填舊列，不刪除原文字欄位。

在上述契約未合併前：

- `significant_diff`／`diff_summary` 只出現在內部 `get_review_case_workspace()` 回應，不寫入任何對外 OpenAPI
  response body，`test_wire_responses_validate_against_openapi_1_0` 仍只驗證嚴格符合既有 schema 的欄位集合。
- `professional_reviewer_id`／`compliance_review_required` 恆為 `null`／`false`，不由任何命令寫入，不代替使用者
  核准尚未存在的「第二覆核人」或「法遵強制複核」政策。
- Agora/Servant、M05 `research.candidate` 即時查詢、M07 report-version 回填均為 `NOT_CONNECTED`；通過本模組
  mock 測試不能視為已打通。
