# ADR-0002｜契約先定與平行開發

狀態：accepted-for-implementation；基準日2026-11-22。

## 背景

用依賴未完成當開工阻塞會把14工作流退回串行，與使用者目標相反。

## 決定

以OpenAPI、JSONSchema、資料catalog、synthetic fixtures及mock提供共同baseline，所有start_dependencies為空。

## 選擇與取捨

模組可以先合併小PR；不以分批合併縮減產品範圍。

## 結果與驗證

coordinator收斂shared changes；provider未完成用fake，最終以全模組整合驗收判定。

對照根目錄AGENTS、SA/SD、API/event/database契約與模組任務包。任何相容性變動須更新受影響UAT。
