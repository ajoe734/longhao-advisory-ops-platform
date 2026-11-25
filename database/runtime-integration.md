# 共用 runtime 與資料交易契約

`longhao_platform` 是 M01–M14 整合完成前即可啟動的 FastAPI 應用 shell；其本機 reference store 不取代各模組的 PostgreSQL repository。正式 business command 必須在同一 PostgreSQL transaction 中完成：

1. 以已驗證 principal 設定 `SET LOCAL app.tenant_id`，不得信任 request 的 tenant header。
2. 鎖定 module aggregate，檢查 `expected_version` 與 `Idempotency-Key` payload fingerprint。
3. 寫入 module-owned business row、`core.audit_event`、`core.outbox` 與命令 receipt；任一步失敗全部 rollback。
4. worker 依 `events/registry.json` 發送 canonical envelope；consumer 先以 `core.inbox` 去重，同 event ID 不同 payload hash 必須拒絕。
5. provider timeout outcome 未知時先 query，不得換 idempotency key 盲目重送退款、訊息或簽章操作。

## 模組登錄

模組整合後提供 `apps/api/src/modules/<manifest slug>/plugin.py`，宣告與 `project/module-manifest.json` 相同的 `MODULE_ID`，並實作 `register(runtime)`。登錄函式可使用：

- `runtime.commands.register(HandlerSpec(...))`：authenticated command handler。
- `runtime.events.register(event_type, module_id, consumer)`：canonical durable event consumer。
- `runtime.include_router(APIRouter(...))`：由 FastAPI router 提供 typed HTTP route；所有 module route 掛在 `/api/v1`。

模組未存在時列為 `missing`；import 或 registration 失敗列為 `broken`。development/test 可在 `degraded` 狀態運行，production configuration 同時禁止 synthetic provider 與 missing module。`release_gate_passed` 只在 14/14 全載入時為 true。

本機 reference store 用純 handler 加單一 commit point 測試 CAS、冪等、audit/outbox 原子性；不可把其記憶體資料當成正式保存或 migration 證據。
