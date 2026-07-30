import json
import os
import boto3

events = boto3.client("events")


def lambda_handler(event, context):
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
