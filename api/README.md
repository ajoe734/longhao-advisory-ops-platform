# API 契約入口

版本1.0.0。全部十四模組同時使用本契約；可在本repo實作adapter，禁止修改Pantheon、LegendFlow等外部repo。

## 檔案與維護

- [OpenAPI 3.1](openapi.yaml)：所有操作的完整request/response/security/error。
- [操作清單](operation-catalog.json)：工作包與RBAC生成依據。
- [外部port](adapter-ports.yaml)：供應商待接通狀態與adapter責任。
- [資料庫投影映射](database-projection-map.yaml)：API讀模型如何由資料authority組成。
- [事件registry](../events/registry.json)：producer/consumer與schema版本。
- [離線mock](../mocks/README.md)：每組agent可獨立進行UI/API開發。

共享契約維護人修改[build_contracts.py](build_contracts.py)後，重新產生OpenAPI、catalog、事件schema/示例與API fixtures；同一PR一起提交。產生器只讀寫本repo，不呼叫供應商。業務文字、mapping與port文件有語意變動時一併更新。

```bash
python api/build_contracts.py
python scripts/validate_contracts.py
python mocks/test_server.py
```

## 覆蓋

|模組|API操作數|
|---|---:|
|M01|13|
|M02|12|
|M03|8|
|M04|8|
|M05|5|
|M06|7|
|M07|8|
|M08|6|
|M09|8|
|M10|10|
|M11|7|
|M12|12|
|M13|14|
|M14|24|

共142個HTTP操作；數量來自catalog，不能將操作數當成功能完成率。每模組真實domain/DB/授權/整合測試仍由對應工作包負責。

## 重要固定規則

- 客戶先簽署；公司承諾、KYC、適合度、AML及權限是各自有證據的狀態。
- M07發布report.signature_verified後，由M08/M09自動發布；沒有第二個人為Publish操作。
- 首次建立report版本即準備可審manifest，review後freeze同hash，內容變動建立新版本。
- company pool七日以全部付費服務中斷解讀為待核定規則；不是七日沒有聯絡。
- CRM歸屬不等於獎金歸屬；業務介入必須具體證據與成功轉換。
- 收入、退款與計獎用decimal字串；未核准池率只能試算，不能正式發放。
- 客服草稿先經approve_service_response_draft取得approval_id，send_service_response才會排程；記事件不等於送訊息。
- 對方介面未知時使用mock並列待接通；十四模組仍平行完成本平台實作。

詳細協議：[API／事件說明](../docs/09-api-events.md)、[外部整合](../docs/04-integrations.md)。
