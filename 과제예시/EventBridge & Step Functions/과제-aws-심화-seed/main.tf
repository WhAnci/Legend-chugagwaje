terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region  = "ap-northeast-2"
  profile = "lee"
}

locals {
  bnum   = "99"
  prefix = "wsc2026"
  account_id = data.aws_caller_identity.current.account_id
}

data "aws_caller_identity" "current" {}

# ──────────────────────────────────────────────────
# DynamoDB
# ──────────────────────────────────────────────────

resource "aws_dynamodb_table" "orders" {
  name         = "${local.prefix}-orders"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "orderId"
  range_key    = "createdAt"

  attribute {
    name = "orderId"
    type = "S"
  }
  attribute {
    name = "createdAt"
    type = "S"
  }
  attribute {
    name = "status"
    type = "S"
  }

  global_secondary_index {
    name            = "status-index"
    hash_key        = "status"
    range_key       = "createdAt"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  deletion_protection_enabled = true
}

resource "aws_dynamodb_table" "order_state" {
  name         = "${local.prefix}-order-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "executionId"

  attribute {
    name = "executionId"
    type = "S"
  }

  deletion_protection_enabled = true
}

# ──────────────────────────────────────────────────
# S3
# ──────────────────────────────────────────────────

resource "aws_s3_bucket" "archive" {
  bucket = "${local.prefix}-orders-archive-${local.bnum}"
}

resource "aws_s3_bucket_versioning" "archive" {
  bucket = aws_s3_bucket.archive.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "archive" {
  bucket = aws_s3_bucket.archive.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "archive" {
  bucket                  = aws_s3_bucket.archive.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "archive" {
  bucket = aws_s3_bucket.archive.id
  rule {
    id     = "intelligent-tiering"
    status = "Enabled"
    filter {}
    transition {
      days          = 30
      storage_class = "INTELLIGENT_TIERING"
    }
  }
}

# ──────────────────────────────────────────────────
# SQS
# ──────────────────────────────────────────────────

resource "aws_sqs_queue" "dlq" {
  name                      = "${local.prefix}-order-dlq"
  message_retention_seconds = 1209600 # 14일
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "main" {
  name                       = "${local.prefix}-order-queue"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 345600 # 4일
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
}

# ──────────────────────────────────────────────────
# EventBridge — 커스텀 이벤트 버스
# ──────────────────────────────────────────────────

resource "aws_cloudwatch_event_bus" "orders" {
  name = "${local.prefix}-order-bus"
}

resource "aws_cloudwatch_event_archive" "orders" {
  name             = "${local.prefix}-order-archive"
  event_source_arn = aws_cloudwatch_event_bus.orders.arn
  retention_days   = 7
}

resource "aws_cloudwatch_event_rule" "success" {
  name           = "${local.prefix}-rule-success"
  event_bus_name = aws_cloudwatch_event_bus.orders.name

  event_pattern = jsonencode({
    source      = ["wsc2026.orders"]
    "detail-type" = ["OrderProcessed"]
  })
}

resource "aws_cloudwatch_event_rule" "failure" {
  name           = "${local.prefix}-rule-failure"
  event_bus_name = aws_cloudwatch_event_bus.orders.name

  event_pattern = jsonencode({
    source      = ["wsc2026.orders"]
    "detail-type" = ["OrderFailed"]
  })
}

# 결과 수신용 큐 (성공 Rule 타겟 — 메인 큐와 분리하여 루프 방지)
resource "aws_sqs_queue" "result" {
  name                    = "${local.prefix}-order-result"
  message_retention_seconds = 86400
  sqs_managed_sse_enabled = true
}

resource "aws_sqs_queue_policy" "result_policy" {
  queue_url = aws_sqs_queue.result.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.result.arn
    }]
  })
}

# 성공 Rule 타겟 → 결과 큐
resource "aws_cloudwatch_event_target" "success_target" {
  rule           = aws_cloudwatch_event_rule.success.name
  event_bus_name = aws_cloudwatch_event_bus.orders.name
  target_id      = "success-to-result-queue"
  arn            = aws_sqs_queue.result.arn
}

# 실패 Rule 타겟 → DLQ (Input Transformer 포함)
resource "aws_cloudwatch_event_target" "failure_target" {
  rule           = aws_cloudwatch_event_rule.failure.name
  event_bus_name = aws_cloudwatch_event_bus.orders.name
  target_id      = "failure-to-dlq"
  arn            = aws_sqs_queue.dlq.arn

  input_transformer {
    input_paths = {
      orderId = "$.detail.orderId"
      reason  = "$.detail.reason"
    }
    input_template = "{\"source\": \"eventbridge\", \"orderId\": \"<orderId>\", \"reason\": \"<reason>\"}"
  }
}

# SQS 리소스 정책 — EventBridge가 메시지 전송 가능하도록
resource "aws_sqs_queue_policy" "dlq_policy" {
  queue_url = aws_sqs_queue.dlq.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.dlq.arn
    }]
  })
}

resource "aws_sqs_queue_policy" "main_policy" {
  queue_url = aws_sqs_queue.main.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.main.arn
    }]
  })
}

# ──────────────────────────────────────────────────
# Step Functions IAM Role
# ──────────────────────────────────────────────────

resource "aws_iam_role" "sfn" {
  name = "${local.prefix}-sfn-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "sfn" {
  name = "${local.prefix}-sfn-policy"
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = [aws_dynamodb_table.orders.arn, aws_dynamodb_table.order_state.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.archive.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["events:PutEvents"]
        Resource = aws_cloudwatch_event_bus.orders.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
                    "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
                    "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"]
        Resource = "*"
      }
    ]
  })
}

# ──────────────────────────────────────────────────
# Step Functions Log Group
# ──────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/states/${local.prefix}-order-workflow"
  retention_in_days = 7
}

# ──────────────────────────────────────────────────
# Step Functions State Machine
# ──────────────────────────────────────────────────

resource "aws_sfn_state_machine" "orders" {
  name     = "${local.prefix}-order-workflow"
  role_arn = aws_iam_role.sfn.arn
  type     = "EXPRESS"

  logging_configuration {
    level                  = "ALL"
    include_execution_data = true
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
  }

  definition = jsonencode({
    Comment = "wsc2026 order processing workflow - Lambda-free SDK integration"
    StartAt = "ExtractBody"
    States = {
      ExtractBody = {
        Type = "Pass"
        Parameters = {
          "order.$" = "States.StringToJson($[0].body)"
        }
        OutputPath = "$.order"
        Next       = "ValidateOrder"
      }

      ValidateOrder = {
        Type = "Choice"
        Choices = [
          {
            And = [
              { Variable = "$.orderId",   IsPresent = true },
              { Variable = "$.product",   IsPresent = true },
              { Variable = "$.quantity",  IsPresent = true },
              { Variable = "$.price",     IsPresent = true },
              { Variable = "$.timestamp", IsPresent = true }
            ]
            Next = "CheckPrice"
          }
        ]
        Default = "HandleError"
      }

      CheckPrice = {
        Type = "Choice"
        Choices = [
          {
            Variable                  = "$.price"
            NumericGreaterThanEquals  = 300000
            Next                      = "ArchiveToS3"
          }
        ]
        Default = "SaveToDynamoDB"
      }

      ArchiveToS3 = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:s3:putObject"
        Parameters = {
          "Bucket"      = "${local.prefix}-orders-archive-${local.bnum}"
          "Key.$"       = "States.Format('orders/{}/{}/{}/{}.json', States.ArrayGetItem(States.StringSplit($.timestamp, '-'), 0), States.ArrayGetItem(States.StringSplit($.timestamp, '-'), 1), States.ArrayGetItem(States.StringSplit(States.ArrayGetItem(States.StringSplit($.timestamp, '-'), 2), 'T'), 0), $.orderId)"
          "Body.$"      = "States.JsonToString($)"
          "ContentType" = "application/json"
        }
        Next = "NotifySuccess"
        Catch = [{ ErrorEquals = ["States.ALL"], Next = "HandleError" }]
      }

      SaveToDynamoDB = {
        Type     = "Task"
        Resource = "arn:aws:states:::dynamodb:putItem"
        Parameters = {
          TableName = "${local.prefix}-orders"
          Item = {
            orderId   = { "S.$" = "$.orderId" }
            createdAt = { "S.$" = "$$.Execution.StartTime" }
            product   = { "S.$" = "$.product" }
            price     = { "N.$" = "States.Format('{}', $.price)" }
            quantity  = { "N.$" = "States.Format('{}', $.quantity)" }
            status    = { S = "COMPLETED" }
          }
        }
        Next = "NotifySuccess"
        Catch = [{ ErrorEquals = ["States.ALL"], Next = "HandleError" }]
      }

      NotifySuccess = {
        Type     = "Task"
        Resource = "arn:aws:states:::events:putEvents"
        Parameters = {
          Entries = [{
            Source       = "wsc2026.orders"
            DetailType   = "OrderProcessed"
            EventBusName = "${local.prefix}-order-bus"
            "Detail.$"   = "States.JsonToString($)"
          }]
        }
        End   = true
        Catch = [{ ErrorEquals = ["States.ALL"], Next = "HandleError" }]
      }

      HandleError = {
        Type     = "Task"
        Resource = "arn:aws:states:::events:putEvents"
        Parameters = {
          Entries = [{
            Source       = "wsc2026.orders"
            DetailType   = "OrderFailed"
            EventBusName = "${local.prefix}-order-bus"
            Detail = {
              "orderId.$" = "$.orderId"
              reason      = "Order processing failed"
            }
          }]
        }
        End = true
      }
    }
  })
}

# ──────────────────────────────────────────────────
# EventBridge Pipes IAM Role
# ──────────────────────────────────────────────────

resource "aws_iam_role" "pipe" {
  name = "${local.prefix}-pipe-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "pipes.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "pipe" {
  name = "${local.prefix}-pipe-policy"
  role = aws_iam_role.pipe.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.main.arn
      },
      {
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = aws_sfn_state_machine.orders.arn
      }
    ]
  })
}

# ──────────────────────────────────────────────────
# EventBridge Pipes
# ──────────────────────────────────────────────────

resource "aws_pipes_pipe" "orders" {
  name     = "${local.prefix}-order-pipe"
  role_arn = aws_iam_role.pipe.arn
  source   = aws_sqs_queue.main.arn
  target   = aws_sfn_state_machine.orders.arn

  source_parameters {
    sqs_queue_parameters {
      batch_size = 1
    }
  }

  target_parameters {
    step_function_state_machine_parameters {
      invocation_type = "FIRE_AND_FORGET"
    }
  }
}

# ──────────────────────────────────────────────────
# API Gateway
# ──────────────────────────────────────────────────

resource "aws_iam_role" "apigw" {
  name = "${local.prefix}-apigw-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "apigateway.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "apigw" {
  name = "${local.prefix}-apigw-policy"
  role = aws_iam_role.apigw.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:SendMessage"]
      Resource = aws_sqs_queue.main.arn
    }]
  })
}

resource "aws_api_gateway_rest_api" "orders" {
  name = "${local.prefix}-order-api"
}

resource "aws_api_gateway_resource" "orders" {
  rest_api_id = aws_api_gateway_rest_api.orders.id
  parent_id   = aws_api_gateway_rest_api.orders.root_resource_id
  path_part   = "orders"
}

resource "aws_api_gateway_request_validator" "orders" {
  name                        = "validate-body"
  rest_api_id                 = aws_api_gateway_rest_api.orders.id
  validate_request_body       = true
  validate_request_parameters = false
}

resource "aws_api_gateway_model" "order_request" {
  rest_api_id  = aws_api_gateway_rest_api.orders.id
  name         = "OrderRequest"
  content_type = "application/json"

  schema = jsonencode({
    "$schema" = "http://json-schema.org/draft-04/schema#"
    type      = "object"
    required  = ["orderId", "product", "quantity", "price"]
    properties = {
      orderId  = { type = "string" }
      product  = { type = "string" }
      quantity = { type = "integer" }
      price    = { type = "number" }
    }
  })
}

resource "aws_api_gateway_method" "post_orders" {
  rest_api_id          = aws_api_gateway_rest_api.orders.id
  resource_id          = aws_api_gateway_resource.orders.id
  http_method          = "POST"
  authorization        = "NONE"
  api_key_required     = true
  request_validator_id = aws_api_gateway_request_validator.orders.id

  request_models = {
    "application/json" = aws_api_gateway_model.order_request.name
  }
}

resource "aws_api_gateway_integration" "post_orders" {
  rest_api_id             = aws_api_gateway_rest_api.orders.id
  resource_id             = aws_api_gateway_resource.orders.id
  http_method             = aws_api_gateway_method.post_orders.http_method
  type                    = "AWS"
  integration_http_method = "POST"
  uri                     = "arn:aws:apigateway:ap-northeast-2:sqs:path/${local.account_id}/${local.prefix}-order-queue"
  credentials             = aws_iam_role.apigw.arn

  request_parameters = {
    "integration.request.header.Content-Type" = "'application/x-www-form-urlencoded'"
  }

  request_templates = {
    "application/json" = "Action=SendMessage&MessageBody=$util.urlEncode($input.body)"
  }
}

resource "aws_api_gateway_method_response" "post_orders_200" {
  rest_api_id = aws_api_gateway_rest_api.orders.id
  resource_id = aws_api_gateway_resource.orders.id
  http_method = aws_api_gateway_method.post_orders.http_method
  status_code = "200"
}

resource "aws_api_gateway_integration_response" "post_orders" {
  rest_api_id = aws_api_gateway_rest_api.orders.id
  resource_id = aws_api_gateway_resource.orders.id
  http_method = aws_api_gateway_method.post_orders.http_method
  status_code = "200"
}

resource "aws_api_gateway_deployment" "orders" {
  rest_api_id = aws_api_gateway_rest_api.orders.id

  depends_on = [
    aws_api_gateway_integration.post_orders,
    aws_api_gateway_integration_response.post_orders
  ]

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_api_gateway_stage" "prod" {
  rest_api_id   = aws_api_gateway_rest_api.orders.id
  deployment_id = aws_api_gateway_deployment.orders.id
  stage_name    = "prod"
}

resource "aws_api_gateway_api_key" "orders" {
  name    = "${local.prefix}-order-key"
  enabled = true
}

resource "aws_api_gateway_usage_plan" "orders" {
  name = "${local.prefix}-order-plan"

  api_stages {
    api_id = aws_api_gateway_rest_api.orders.id
    stage  = aws_api_gateway_stage.prod.stage_name
  }

  throttle_settings {
    rate_limit  = 10
    burst_limit = 20
  }

  quota_settings {
    limit  = 1000
    period = "DAY"
  }
}

resource "aws_api_gateway_usage_plan_key" "orders" {
  key_id        = aws_api_gateway_api_key.orders.id
  key_type      = "API_KEY"
  usage_plan_id = aws_api_gateway_usage_plan.orders.id
}

# ──────────────────────────────────────────────────
# CloudWatch Alarm + Dashboard
# ──────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  alarm_name          = "${local.prefix}-dlq-depth-alarm"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Sum"
  threshold           = 1

  dimensions = {
    QueueName = aws_sqs_queue.dlq.name
  }

  alarm_description = "DLQ에 메시지가 쌓였습니다"
  treat_missing_data = "notBreaching"
}

resource "aws_cloudwatch_dashboard" "orders" {
  dashboard_name = "${local.prefix}-order-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric"
        properties = {
          title   = "API Gateway 5XX 에러율"
          region  = "ap-northeast-2"
          metrics = [["AWS/ApiGateway", "5XXError", "ApiName", "${local.prefix}-order-api"]]
          period  = 60
          stat    = "Sum"
          view    = "timeSeries"
        }
      },
      {
        type = "metric"
        properties = {
          title   = "SQS DLQ 메시지 수"
          region  = "ap-northeast-2"
          metrics = [["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", "${local.prefix}-order-dlq"]]
          period  = 60
          stat    = "Maximum"
          view    = "timeSeries"
        }
      },
      {
        type = "metric"
        properties = {
          title  = "Step Functions 실행 성공/실패"
          region = "ap-northeast-2"
          metrics = [
            ["AWS/States", "ExecutionsSucceeded", "StateMachineArn", aws_sfn_state_machine.orders.arn],
            ["AWS/States", "ExecutionsFailed",    "StateMachineArn", aws_sfn_state_machine.orders.arn]
          ]
          period = 60
          stat   = "Sum"
          view   = "timeSeries"
        }
      },
      {
        type = "metric"
        properties = {
          title  = "EventBridge Rule 매칭 횟수"
          region = "ap-northeast-2"
          metrics = [
            ["AWS/Events", "MatchedEvents", "EventBusName", "${local.prefix}-order-bus", "RuleName", "${local.prefix}-rule-success"],
            ["AWS/Events", "MatchedEvents", "EventBusName", "${local.prefix}-order-bus", "RuleName", "${local.prefix}-rule-failure"]
          ]
          period = 60
          stat   = "Sum"
          view   = "timeSeries"
        }
      }
    ]
  })
}

# ──────────────────────────────────────────────────
# Outputs
# ──────────────────────────────────────────────────

output "api_endpoint" {
  value = "${aws_api_gateway_stage.prod.invoke_url}/orders"
}

output "api_key_id" {
  value = aws_api_gateway_api_key.orders.id
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.orders.arn
}

output "archive_bucket" {
  value = aws_s3_bucket.archive.id
}
