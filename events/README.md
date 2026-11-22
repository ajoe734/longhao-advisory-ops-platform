# 事件契約

[registry.json](registry.json)列出全部事件的authority與consumer；`schemas/`是JSON Schema 2020-12封閉信封及payload；`examples/`完全合成，無真實客戶、名單命中或付款。

每個事件從同資料庫交易的outbox發出，至少一次投遞，inbox唯一鍵為consumer/tenant/event_id。相同event_id不同payload_sha256視為衝突並隔離。只有完整訂閱aggregate流的consumer可要求sequence_no逐次連續；部分訂閱者要用版本及source查證處理跳號。

`report.signature_verified`由M07唯一發布，M08消費後立即自動發布；沒有人工第二次Publish。`publication.created`帶給M09的是內容版次、簽章與受眾快照引用；實際dispatch仍再次檢查客戶權限、簽章撤銷、訊息期限與渠道可用性。

`aml.case_changed`只在AML受限topic，不能進通用CRM、客服或LLM。跨module只發`business_clearance.changed`安全摘要；event payload中不可追加原始證件、客戶姓名、案件調查敘述、token或存取URL。

先執行`python scripts/validate_contracts.py`；實作時再加業務成功、拒絕、duplicate、out-of-order、schema升版、hash衝突、worker crash、lease過期、dead-letter、redrive與撤權競態測試。詳細規則在[API與事件說明](../docs/09-api-events.md)。
