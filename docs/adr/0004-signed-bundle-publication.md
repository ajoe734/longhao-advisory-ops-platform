# ADR-0004｜整包簽署與自動發布

狀態：accepted-for-implementation；基準日2026-11-22。

## 背景

報告與簡訊可能不同步；簽後再生成投資文字會失去版本責任。

## 決定

簽章前freeze report、recommendation、channel renders、audience policy；驗證成功事件直接由system發布。

## 選擇與取捨

不要第二人工publish gate，也不把AI生成或Pantheon策略批准當正式簽章。

## 結果與驗證

只接受exact hash與有效review；更正新版本，新訊息保留原history。

對照根目錄AGENTS、SA/SD、API/event/database契約與模組任務包。任何相容性變動須更新受影響UAT。
