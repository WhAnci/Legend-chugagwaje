# Lambda 예시 코드 — 이벤트 드리븐 서버리스 파이프라인

> 아래 코드는 **참고용 골격**입니다. 환경변수 이름, ARN, 분기 조건은 과제 스펙에 맞게 수정하세요.

---

## 공통 환경변수

| Key | 값 |
|---|---|
| `STATE_MACHINE_ARN` | Step Functions ARN |
| `ORDERS_TABLE` | `wsc2026-orders` |
| `STATE_TABLE` | `wsc2026-order-state` |
| `ARCHIVE_BUCKET` | `wsc2026-orders-archive-<비번호>` |
| `EVENT_BUS_NAME` | `wsc2026-order-bus` |

---

## 1. wsc2026-validator

SQS 트리거 → 필드 검증 → Step Functions 실행 기동

```python
import json
import os
import boto3
from datetime import datetime, timezone

sfn = boto3.client("stepfunctions")

REQUIRED_FIELDS = {"orderId", "product", "quantity", "price", "timestamp"}

def lambda_handler(event, context):
    for record in event["Records"]:
        body = json.loads(record["body"])

        missing = REQUIRED_FIELDS - body.keys()
        if missing:
            raise ValueError(f"Missing fields: {missing}")

        if not isinstance(body["price"], (int, float)) or body["price"] <= 0:
            raise ValueError("price must be a positive number")
        if not isinstance(body["quantity"], int) or body["quantity"] <= 0:
            raise ValueError("quantity must be a positive integer")

        sfn.start_execution(
            stateMachineArn=os.environ["STATE_MACHINE_ARN"],
            name=f"{body['orderId']}-{int(datetime.now(timezone.utc).timestamp())}",
            input=json.dumps(body),
        )
```

---

## 2. wsc2026-processor

Step Functions Task → price 분기 → DynamoDB 또는 S3 저장

```python
import json
import os
import boto3
from datetime import datetime, timezone

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

HIGH_VALUE_THRESHOLD = 300_000

def lambda_handler(event, context):
    order = event  # Step Functions가 input을 그대로 전달
    order_id = order["orderId"]
    price = order["price"]
    created_at = datetime.now(timezone.utc).isoformat()

    if price >= HIGH_VALUE_THRESHOLD:
        today = datetime.now(timezone.utc)
        key = f"orders/{today.year}/{today.month:02d}/{today.day:02d}/{order_id}.json"
        s3.put_object(
            Bucket=os.environ["ARCHIVE_BUCKET"],
            Key=key,
            Body=json.dumps({**order, "createdAt": created_at}),
            ContentType="application/json",
        )
        storage = "s3"
    else:
        table = dynamodb.Table(os.environ["ORDERS_TABLE"])
        table.put_item(Item={
            **order,
            "createdAt": created_at,
            "status": "COMPLETED",
        })
        storage = "dynamodb"

    return {**order, "createdAt": created_at, "storage": storage, "status": "COMPLETED"}
```

---

## 3. wsc2026-notifier

Step Functions Task → EventBridge 커스텀 이벤트 버스에 결과 발행

```python
import json
import os
import boto3

events = boto3.client("events")

def lambda_handler(event, context):
    # event: processor가 반환한 주문 객체 (status, storage 포함)
    detail_type = "OrderFailed" if event.get("status") == "FAILED" else "OrderProcessed"

    events.put_events(
        Entries=[
            {
                "Source": "wsc2026.orders",
                "DetailType": detail_type,
                "Detail": json.dumps(event),
                "EventBusName": os.environ["EVENT_BUS_NAME"],
            }
        ]
    )

    return event
```

---

## Step Functions ASL 골격 (참고)

```json
{
  "Comment": "wsc2026 order processing workflow",
  "StartAt": "ValidateOrder",
  "States": {
    "ValidateOrder": {
      "Type": "Task",
      "Resource": "<wsc2026-validator ARN>",
      "Next": "ProcessOrder",
      "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "HandleError"}]
    },
    "ProcessOrder": {
      "Type": "Task",
      "Resource": "<wsc2026-processor ARN>",
      "Next": "NotifyResult",
      "Catch": [{"ErrorEquals": ["States.ALL"], "Next": "HandleError"}]
    },
    "NotifyResult": {
      "Type": "Task",
      "Resource": "<wsc2026-notifier ARN>",
      "End": true
    },
    "HandleError": {
      "Type": "Task",
      "Resource": "<wsc2026-notifier ARN>",
      "Parameters": {
        "status": "FAILED",
        "reason.$": "$.Cause"
      },
      "End": true
    }
  }
}
```
