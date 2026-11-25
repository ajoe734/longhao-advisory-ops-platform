# FastAPI 共用應用

`longhao_platform` 提供十四模組共同的 composition root、認證 principal、command bus、canonical event registry、audit/outbox/inbox reference semantics 與健康狀態。M01–M14 各自只在 manifest 指定目錄提供 `plugin.py`，由 `register(runtime)` 登錄 FastAPI router、command handler 與 event consumer。

本機啟動：

```bash
python -m pip install -e apps/api
longhao-api --check
longhao-api --port 8080
```

development 預設只接受兩個明確的合成 marker token，且只綁定固定合成 tenant。production 啟動會拒絕 synthetic provider、缺少模組及預設 authenticator。這個 reference store 不取代 PostgreSQL；正式 module repository 必須遵守 `database/runtime-integration.md` 的同交易規則。
