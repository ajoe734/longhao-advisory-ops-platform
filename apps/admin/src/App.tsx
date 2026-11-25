import { useEffect, useMemo, useState } from "react";
import { currentPath, fetchReadiness, matchScreen, screens, type Readiness } from "./platform";
import "./styles.css";

type LoadState =
  | { kind: "loading" }
  | { kind: "loaded"; readiness: Readiness }
  | { kind: "error"; reference: string };

function useHashPath(): string {
  const [path, setPath] = useState(currentPath);
  useEffect(() => {
    const update = () => setPath(currentPath());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return path;
}

export function App() {
  const path = useHashPath();
  const [loadState, setLoadState] = useState<LoadState>({kind: "loading"});
  const selected = matchScreen(path);
  const groups = useMemo(() => [...new Set(screens.map((screen) => screen.group))], []);

  useEffect(() => {
    const controller = new AbortController();
    fetchReadiness(controller.signal)
      .then((readiness) => setLoadState({kind: "loaded", readiness}))
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setLoadState({kind: "error", reference: error instanceof Error ? error.message : "UNKNOWN_ERROR"});
        }
      });
    return () => controller.abort();
  }, []);

  const moduleStatus =
    selected?.moduleId === "SHARED" || loadState.kind !== "loaded"
      ? undefined
      : loadState.readiness.modules?.find((module) => module.module_id === selected?.moduleId);

  return (
    <>
      <a className="skip-link" href="#main">跳到主要內容</a>
      <div className="app-shell">
        <header className="topbar">
          <div>
            <p className="eyebrow">Longhao advisory operations</p>
            <h1>龍好營運管理平台</h1>
          </div>
          <div className={`status ${loadState.kind}`} role="status" aria-live="polite">
            {loadState.kind === "loading" && "正在檢查模組…"}
            {loadState.kind === "error" && `狀態服務失敗・${loadState.reference}`}
            {loadState.kind === "loaded" &&
              (loadState.readiness.status === "ready"
                ? "14/14 模組已載入"
                : `${loadState.readiness.loaded_modules}/14 已載入・${loadState.readiness.status === "degraded" ? "本機降級模式" : "平台未就緒"}`)}
          </div>
        </header>
        <aside className="sidebar" aria-label="主要導覽">
          <nav>
            {groups.map((group) => (
              <section className="nav-group" key={group}>
                <h2>{group}</h2>
                {screens.filter((screen) => screen.group === group).map((screen) => (
                  <a
                    href={`#${screen.path.replace(":id", "synthetic-id")}`}
                    aria-current={screen.id === selected?.id ? "page" : undefined}
                    key={screen.id}
                  >
                    {screen.title}
                  </a>
                ))}
              </section>
            ))}
          </nav>
        </aside>
        <main id="main" tabIndex={-1}>
          <section className="page-heading">
            <p className="eyebrow">{selected?.id ?? "ROUTE-NOT-FOUND"}・{selected?.moduleId ?? "共用"}</p>
            <h2>{selected?.title ?? "找不到畫面"}</h2>
            <p>共用 shell 僅呈現整合狀態；模組功能在對應 workstream 合併後登錄。</p>
          </section>
          {!selected && (
            <section className="panel error" role="alert">
              <h3>畫面不存在</h3>
              <p>請從導覽選擇已定義的 34 個營運畫面之一。</p>
            </section>
          )}
          {selected && loadState.kind === "loading" && (
            <section className="panel" aria-busy="true"><h3>模組狀態</h3><p>載入中…</p></section>
          )}
          {selected && loadState.kind === "error" && (
            <section className="panel error" role="alert"><h3>無法載入</h3><p>這不是空資料。參考代碼：{loadState.reference}</p></section>
          )}
          {selected && loadState.kind === "loaded" && (
            <section className="panel">
              <h3>模組狀態</h3>
              {selected.moduleId === "SHARED" ? (
                <p>平台共用功能；目前載入 {loadState.readiness.loaded_modules}/14 個模組。</p>
              ) : moduleStatus ? (
                <>
                  <p><strong>{moduleStatus.module_id} {moduleStatus.title}</strong></p>
                  <span className={`badge ${moduleStatus.status}`}>
                    {moduleStatus.status === "loaded" ? "已整合" : moduleStatus.status === "broken" ? "載入失敗" : "尚未整合"}
                  </span>
                  {moduleStatus.error_code && <p>參考代碼：{moduleStatus.error_code}</p>}
                </>
              ) : (
                <p>模組明細在此環境不公開；請由授權的營運狀態端點查詢。</p>
              )}
            </section>
          )}
          <section className="panel notice">
            <h3>安全執行模式</h3>
            <p>本機環境只使用合成資料與 local provider。未整合、空清單或 mock 回應都不代表通知、付款、簽章或正式發布已成功。</p>
          </section>
        </main>
      </div>
    </>
  );
}
