# 19｜文件、版本與需求變更控制

## 權威分工

最新使用者決定定義範圍；公司核定policy定義正式業務參數；SA/模組規格定義結果；OpenAPI與事件schema定義對接；database catalog/migrations定義持久資料；UAT定義可觀察驗收。各自用途不同，出現衝突應修正所有受影響契約，不由agent隨便選一份壓過其他份。

本版spec baseline為2.0、API/event各依schema版本。Git commit SHA精確識別交付；README日期不是不可變版本。每次改動附REQ/module IDs、schema差異、migration相容性與UAT更新。

## 相容變更

新增可選欄位或新operation可minor；刪/改必填、enum縮減、語義變更必須新major或雙版本轉接。event payload一經發出不改相同版本的原意；schema evolution保留舊consumer驗證。DB採expand→backfill→switch→contract，舊簽章/帳務證據不得改寫。

## 三種待辦

工程缺陷可依本規格修復；業務政策未定案以draft策略實作與試算；外部依賴缺口只寫本平台adapter gap。後兩者不阻止其他工作流，也不授權修改外部專案。不要把不明確要求默默變成已核准規則。

## 文件完整性

CI驗相對連結、十四模組覆蓋、API→UAT、資料表引用及policy draft gate。人員review確認業務意義、狀態/時序/例外與責任分工。機器檢查通過不等於法遵核定或最終使用者驗收。
