# ADR-0007｜LLM草稿與決策權分離

狀態：accepted-for-implementation；基準日2026-11-22。

## 背景

庶務需要減人力，專業/法遵/財務權限不能因接上LLM而失去核決。

## 決定

LLM gateway限任務/資料/工具、schema驗證、來源引用、版本與成本；deterministic程式處理正式狀態。

## 選擇與取捨

不以模糊的human-in-loop口號取代具體role；不得讓model生成approved直接核決。

## 結果與驗證

AI不可用仍能核心營運；建立例外queue和低風險FAQ白名單。

對照根目錄AGENTS、SA/SD、API/event/database契約與模組任務包。任何相容性變動須更新受影響UAT。
