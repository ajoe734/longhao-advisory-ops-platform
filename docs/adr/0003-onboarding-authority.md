# ADR-0003｜客戶簽署與公司承諾分離

狀態：accepted-for-implementation；基準日2026-11-22。

## 背景

使用者指定先簽後KYC；原制度p5/p9有待修訂措辭，不能把各事實壓成一個signed旗標。

## 決定

分開signature、review、company acceptance、contract effective及entitlement，前端按使用者順序進行。

## 選擇與取捨

不把付款或客戶簽署當公司承諾；不改回先KYC才允許客戶簽。

## 結果與驗證

實作完整state/gate與時間；正式條款及制度修訂獨立核定，草案不冒充批准。

對照根目錄AGENTS、SA/SD、API/event/database契約與模組任務包。任何相容性變動須更新受影響UAT。
