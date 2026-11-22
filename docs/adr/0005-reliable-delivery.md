# ADR-0005｜Outbox、Inbox與外部對帳

狀態：accepted-for-implementation；基準日2026-11-22。

## 背景

DBcommit與外部送訊/收款不能原子提交，callback也可能重複失序或遺失回應。

## 決定

業務+audit+outbox同交易；consumer inbox+effect同交易；external lookup/reconciliation處理unknown。

## 選擇與取捨

不宣稱外部exactly-once；不把timeout當失敗重付重發。

## 結果與驗證

同event同hash去重，異hash衝突；lease/DLQ/redrive可見，發送前再驗有效性。

對照根目錄AGENTS、SA/SD、API/event/database契約與模組任務包。任何相容性變動須更新受影響UAT。
