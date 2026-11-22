# ADR-0006｜收入、歸屬與獎金獨立

狀態：accepted-for-implementation；基準日2026-11-22。

## 背景

自助續訂不能因assignment存在就付業務，預收現金亦不是已賺服務收入。

## 決定

finance確認的period revenue作base；source/maintenance/opportunity/conversion/payee分開，政策快照可重算。

## 選擇與取捨

不採未批准3%默認，也不硬選衰減與30/70的解讀。

## 結果與驗證

draft政策可試算不可paid；退款按原entry/policy/payee追加調整。

對照根目錄AGENTS、SA/SD、API/event/database契約與模組任務包。任何相容性變動須更新受影響UAT。
