import json
import os
import boto3
from datetime import datetime, timezone

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

HIGH_VALUE_THRESHOLD = 300_000


def lambda_handler(event, context):
    order = event
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
