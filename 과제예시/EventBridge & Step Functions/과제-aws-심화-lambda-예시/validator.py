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
