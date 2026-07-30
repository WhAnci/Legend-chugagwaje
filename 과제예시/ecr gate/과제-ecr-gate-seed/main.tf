##############################################
# 제7과제 정답 인프라 (출제자 검증용 · 비공개)
# ECR 강화스캔 → EventBridge → gate Lambda → prod 승격 → ECS
#
# 주의: 이미지(v1/v2) 푸시는 terraform 밖에서 build-push.sh 로 먼저 수행한다.
#       레지스트리 스캔 설정이 켜진 뒤에 푸시해야 스캔 이벤트가 발생한다.
##############################################

terraform {
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 5.0" }
    archive = { source = "hashicorp/archive" }
  }
}

variable "profile" { default = "lee" }
variable "num" { default = "01" }

provider "aws" {
  region  = "ap-northeast-2"
  profile = var.profile
}

data "aws_caller_identity" "me" {}
data "aws_region" "current" {}

locals {
  repo_name = "wsc2026-shopd-${var.num}"
}

##############################################
# ECR + 레지스트리 강화 스캔
##############################################

resource "aws_ecr_repository" "shopd" {
  name                 = local.repo_name
  image_tag_mutability = "MUTABLE"
  force_delete         = true
}

resource "aws_inspector2_enabler" "ecr" {
  account_ids    = [data.aws_caller_identity.me.account_id]
  resource_types = ["ECR"]
}

resource "aws_ecr_registry_scanning_configuration" "this" {
  scan_type  = "ENHANCED"
  depends_on = [aws_inspector2_enabler.ecr]

  rule {
    scan_frequency = "SCAN_ON_PUSH"
    repository_filter {
      filter      = "wsc2026-shopd-*"
      filter_type = "WILDCARD"
    }
  }
}

##############################################
# gate Lambda
##############################################

data "archive_file" "gate" {
  type        = "zip"
  source_file = "${path.module}/../과제-ecr-gate-지급파일/gate/gate.py"
  output_path = "${path.module}/gate.zip"
}

resource "aws_iam_role" "gate" {
  name = "wsc2026-gate-role-${var.num}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# 최소권한: 스캔 결과 조회 + 태그 부여 + 로그. 관리형 FullAccess 는 쓰지 않는다.
resource "aws_iam_role_policy" "gate" {
  name = "gate-policy"
  role = aws_iam_role.gate.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:DescribeImageScanFindings",
          "ecr:BatchGetImage",
          "ecr:PutImage",
          "ecr:DescribeImages",
          "ecr:GetAuthorizationToken",
        ]
        Resource = "*"
      },
      {
        # 강화 스캔에서 ecr:DescribeImageScanFindings 는 Inspector 조회 권한을 함께 요구한다.
        Effect = "Allow"
        Action = [
          "inspector2:ListFindings",
          "inspector2:ListCoverage",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "gate" {
  name              = "/aws/lambda/wsc2026-gate-${var.num}"
  retention_in_days = 1
}

resource "aws_lambda_function" "gate" {
  function_name    = "wsc2026-gate-${var.num}"
  role             = aws_iam_role.gate.arn
  runtime          = "python3.12"
  handler          = "gate.handler"
  filename         = data.archive_file.gate.output_path
  source_code_hash = data.archive_file.gate.output_base64sha256
  timeout          = 60

  environment {
    variables = {
      REPO        = local.repo_name
      PROMOTE_TAG = "prod"
    }
  }

  depends_on = [aws_cloudwatch_log_group.gate]
}

##############################################
# EventBridge — 스캔 완료 이벤트 → gate
##############################################

resource "aws_cloudwatch_event_rule" "gate" {
  name        = "wsc2026-gate-rule-${var.num}"
  description = "image scan completed to gate"
  state       = "ENABLED"

  event_pattern = jsonencode({
    source        = ["aws.inspector2"]
    "detail-type" = ["Inspector2 Scan"]
    detail = {
      "scan-status" = ["INITIAL_SCAN_COMPLETE"]
    }
  })
}

resource "aws_cloudwatch_event_target" "gate" {
  rule = aws_cloudwatch_event_rule.gate.name
  arn  = aws_lambda_function.gate.arn
}

resource "aws_lambda_permission" "events" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.gate.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.gate.arn
}

##############################################
# 네트워크 (ECS 태스크용 퍼블릭 서브넷)
##############################################

data "aws_availability_zones" "az" { state = "available" }

resource "aws_vpc" "this" {
  cidr_block           = "10.30.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "wsc2026-gate-vpc-${var.num}" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
}

resource "aws_subnet" "pub" {
  count                   = 2
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet("10.30.0.0/16", 8, count.index)
  availability_zone       = data.aws_availability_zones.az.names[count.index]
  map_public_ip_on_launch = true
}

resource "aws_route_table" "pub" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
}

resource "aws_route_table_association" "pub" {
  count          = 2
  subnet_id      = aws_subnet.pub[count.index].id
  route_table_id = aws_route_table.pub.id
}

resource "aws_security_group" "task" {
  name        = "wsc2026-gate-task-sg-${var.num}"
  description = "shopd task"
  vpc_id      = aws_vpc.this.id

  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

##############################################
# ECS — :prod 태그만 참조
##############################################

resource "aws_ecs_cluster" "this" {
  name = "wsc2026-gate-cluster-${var.num}"
}

resource "aws_iam_role" "exec" {
  name = "wsc2026-gate-exec-role-${var.num}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "exec" {
  role       = aws_iam_role.exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_cloudwatch_log_group" "task" {
  name              = "/ecs/wsc2026-shopd-${var.num}"
  retention_in_days = 1
}

resource "aws_ecs_task_definition" "shopd" {
  family                   = "wsc2026-shopd-task-${var.num}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.exec.arn

  container_definitions = jsonencode([{
    name      = "shopd"
    image     = "${aws_ecr_repository.shopd.repository_url}:prod"
    essential = true
    portMappings = [{
      containerPort = 8080
      protocol      = "tcp"
    }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.task.name
        "awslogs-region"        = data.aws_region.current.name
        "awslogs-stream-prefix" = "shopd"
      }
    }
  }])
}

resource "aws_ecs_service" "shopd" {
  name            = "wsc2026-shopd-svc-${var.num}"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.shopd.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.pub[*].id
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true
  }
}

output "repo_url" { value = aws_ecr_repository.shopd.repository_url }
output "repo_name" { value = local.repo_name }
