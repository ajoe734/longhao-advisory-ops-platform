# ADR-0009｜資料owner與受限AML

狀態：accepted-for-implementation；基準日2026-11-22。

## 背景

共用Postgres不代表每個模組都能寫所有資料或讀AML全文。

## 決定

跨tenant composite FK、runtime RLS、module roles、restricted AML schema；只輸出最小business clearance。

## 選擇與取捨

不使用superuser app login或把14role全塞同一runtime login。

## 結果與驗證

以正常DBserver測role/RLS；standalone測試僅證明SQL/constraint，不能冒稱隔離通過。

對照根目錄AGENTS、SA/SD、API/event/database契約與模組任務包。任何相容性變動須更新受影響UAT。
