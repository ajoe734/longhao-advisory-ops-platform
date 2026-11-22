# 十四模組共用離線 Mock

這個目錄讓十四個模組同時開發。它提供完整API形狀、固定合成response及指定錯誤情境，不執行真正KYC審核、簽章、付款、發布、計獎或發訊息。請勿提交真實資料、真實token或供應商private key。

## 啟動與驗證

```bash
python scripts/validate_contracts.py
python mocks/test_server.py
python mocks/server.py --port 8765
```

server只綁定`127.0.0.1`。`GET /health`明確顯示`business_logic: false`。完整request樣本位於`fixtures.json`，key為`api/operation-catalog.json`的operation_id。

## 呼叫

每次呼叫加`X-Mock-Mode: synthetic`。一般API加下列其中一個明確合成marker：

- `Authorization: Bearer synthetic-staff`
- `Authorization: Bearer synthetic-customer`
- `Authorization: Bearer synthetic-service`

這些marker只有mock辨識作用，不實作OIDC、MFA、角色或tenant授權。生產JWT、RBAC/ABAC、SoD與資源存取需模組實作及安全測試。

非GET操作加`Idempotency-Key: synthetic-request-0001`；catalog標示compare_and_set的操作還需`If-Match: "1"`。所有fixture的ETag固定為1。相同key/payload重放回原response，異payload回409；mock的重放記憶體只存活到process結束，正式服務需持久transactional idempotency。

整合callback另加`X-Webhook-Timestamp`、`X-Webhook-Nonce`、`X-Integration-Key-Id`與`X-Webhook-Signature: synthetic-valid-signature`。**此值只是mock marker，不是真實HMAC簽章實作**；正式adapter依[串接文件](../docs/04-integrations.md)驗簽、key rotation與重播保護。

## 不依賴手動複製JSON的呼叫示例

在repo根目錄啟動server後，另一terminal可執行：

```bash
python - <<'PY'
import json
from urllib.request import Request, urlopen
fixture = json.load(open('mocks/fixtures.json'))['create_application']['request']
request = Request('http://127.0.0.1:8765/api/v1/applications',
    data=json.dumps(fixture).encode(),
    headers={'Content-Type':'application/json',
             'X-Mock-Mode':'synthetic',
             'Authorization':'Bearer synthetic-customer',
             'Idempotency-Key':'synthetic-example-0001'})
with urlopen(request) as response:
    print(response.status)
    print(json.load(response))
PY
```

## 錯誤情境

設`X-Mock-Scenario`為以下值，其餘request須先符合schema：

|情境|HTTP|預期UI或consumer反應|
|---|---|---|
|`success`|catalog定義|顯示持久接收／完成之差別|
|`forbidden`|403|顯示無權限，不重試|
|`conflict`|412|重讀版本，顯示差異，不能靜默覆蓋|
|`invalid_transition`|409|保留使用者輸入，顯示不允許動作原因|
|`policy_unapproved`|409|獎金可試算，不能入正式發放|
|`dependency_unavailable`|503|顯示外部不可用，按Retry-After排程|
|`ambiguous_timeout`|503|`retryable=false`；先向供應商查原reference|
|`rate_limited`|429|退避，不建立重複任務|
|`expired`|409|停止逾期建議投遞|
|`entitlement_denied`|403|停用當次存取／發送，不因快照曾授權而放行|

HTTP mock不模擬所有跨事件業務狀態。失序/重複event、付款對帳及財務計算應使用events fixtures、DB synthetic seeds及各module domain tests。執行八個HTTP測試不能宣稱十四模組實作已完成。
