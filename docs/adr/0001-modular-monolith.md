# ADR-0001｜模組化單體與十四工作流

狀態：accepted-for-implementation；基準日2026-11-22。

## 背景

同時交付14模組需清楚邊界，但現階段拆成14部署服務會增加跨服務交易、憑證、監控與維運負擔。

## 決定

採獨立domain module/schema/ports的單體API與可靠worker；前後端feature各自ownership，14worktree平行。

## 選擇與取捨

不用單一巨型service類別；不用為平行開發而強制微服務。

## 結果與驗證

跨域寫入經owner；將來依實際負載/隔離需求拆出服務，先維持契約不變。

對照根目錄AGENTS、SA/SD、API/event/database契約與模組任務包。任何相容性變動須更新受影響UAT。
