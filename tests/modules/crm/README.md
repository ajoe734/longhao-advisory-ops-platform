# M10 CRM 測試

```bash
PYTHONPATH=apps/api/src:workers/tasks python3 -m pytest tests/modules/crm -q
```

（本 repo 的 `tests/modules/*` 慣例是以 `conftest.py` 自行把
`apps/api/src`／`workers/tasks` 插進 `sys.path`，所以單獨對本目錄
`pytest tests/modules/crm` 也能跑；上面指令只是在 CI/本機未安裝套件成
editable 時多一層保險。）

## 檔案

- `conftest.py`：`repository`／`evidence`／`service`／`consumer`／
  `event_bus`／`outbox_publisher`／`sweep_worker` fixtures；`Actors` 提供
  固定的 sales/sales_2/supervisor/service_agent/marketing 身分。
- `envelope.py`：`make_envelope` 產生符合 canonical envelope 形狀、
  `payload_sha256` 正確計算的合成事件（不是真實上游系統）。獨立於
  `conftest.py`、使用本目錄唯一的 basename：一個裸的
  `from conftest import ...` 在同一 pytest session 內收集多個
  `tests/modules/*` 套件時，解析到的不保證是本目錄自己的
  `conftest.py`（取決於哪個同名 `conftest` 模組最後被載入進
  `sys.modules`），所以共用的小工具改放在不叫 `conftest` 的模組。
- `test_crm_uat.py`：[逐案驗收](../../../docs/modules/M10-crm.md#14-逐案驗收)
  UAT-M10-001 ~ 012，見 `traceability.md`。
- `test_crm_contract_worker_http.py`：idempotency replay/conflict、CAS
  412/428、`M10Controller` 的 problem+json 映射、consumer 的
  payload-hash-mismatch／sequence-gap／不支援事件型別、
  `OutboxPublisher` 的 unknown-result-via-lookup 與 dead-letter 演練。
- `test_crm_security_automation.py`：角色 SoD 邊界（跨人機會存取、自我認領
  禁止、maker-checker）與 `daily_contact_draft` 自動化能力的輸入最小化、
  角色限制及審閱流程。
- `test_crm_ui_contract.py`：`allowed_actions` 由伺服器計算、suppression
  投影對 UI 可見狀態的影響、`list_crm_worklists`／`get_funnel_metrics` 回應
  形狀。

檔名刻意加上 `crm_` 前綴（而非沿用 `test_uat.py` 等泛用名）：本 repo 的
`tests/modules/*` 目錄彼此沒有 `__init__.py`，pytest 在沒有套件結構時以
檔案 basename 當作全域 module 名稱；與其他模組（例如
`tests/modules/customer_identity_application`）同名的測試檔案會在
`pytest tests/`（跨模組一次收集）時產生 `import file mismatch` 收集錯誤。

## 已知限制

- 全部測試直接呼叫 `CrmService`／`M10EventConsumer`，不經過
  `longhao_platform.http` 的 FastAPI router；本模組尚未提供
  `plugin.py`／`register(runtime)`（見
  `apps/api/src/modules/crm/README.md`），`longhao_platform.app.SyntheticAuthenticator`
  目前也只提供 `platform_admin`/`internal_auditor`/`platform_worker`，沒有
  `sales`/`sales_supervisor`/`marketing`/`service_agent` 的測試
  principal，無法端到端走完整授權路徑。
- `core.object_asset` 存在性以 `FakeEvidencePort` 模擬；
  `database/proposals/M10` 尚未併入正式 migration 前，無法對真實
  PostgreSQL 執行對應 constraint 測試。
- Pantheon／Agora／LegendFlow／金流／簽章等外部系統僅以
  `events/examples`、合成 envelope 模擬；通過本套件不代表已與對方系統
  打通。
