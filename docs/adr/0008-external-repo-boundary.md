# ADR-0008｜外部專案禁止修改

狀態：accepted-for-implementation；基準日2026-11-22。

## 背景

使用者明示Pantheon與LegendFlow只需預留串接規格，最多實作本平台adapter，不可修改兩專案。

## 決定

只在本repo寫契約、mapping、fixtures、mock與adapter；Agora/Servant亦沿用外部界線。

## 選擇與取捨

不在外部repo提交commit/PR、設定或部署，也不以對方缺endpoint作為擅改理由。

## 結果與驗證

外部gap獨立記錄；本平台全部14模組照常開發，真sandbox連線另列readiness。

對照根目錄AGENTS、SA/SD、API/event/database契約與模組任務包。任何相容性變動須更新受影響UAT。
