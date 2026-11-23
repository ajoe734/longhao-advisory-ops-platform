# React／TypeScript 管理介面 shell

共用 shell 登錄 `docs/10-ui-ux.md` 的 UI-01～UI-34，呈現 loading、degraded、broken、route-not-found 與狀態服務失敗，不把 backend failure 當成空清單。M01–M14 feature 合併後由 UI steward 接入對應 route。

```bash
npm install --ignore-scripts --no-audit --no-fund
npm run build
npm run dev
```

Vite dev server 只綁 `127.0.0.1`，並把 `/health`、`/internal`、`/api` 代理到本機 FastAPI。正式 build 使用 `/admin/` base，可由 FastAPI 掛載 `dist`。

目前直接與開發相依已 exact pin；此受限執行環境無法連 npm registry，因此尚未產生可審查的 `package-lock.json`。registry-enabled 整合環境應先生成並審查鎖檔，再把 CI 的 `npm install` 收斂為 `npm ci`。
