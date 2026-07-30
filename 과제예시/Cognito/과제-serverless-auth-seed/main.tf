terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

variable "bnum" {
  description = "비번호 (예: 01)"
  default     = "99"
}

locals {
  prefix = "wsc2026-auth"
  region = "ap-northeast-2"

  cluster_name  = "${local.prefix}-cluster-${var.bnum}"
  service_name  = "${local.prefix}-service-${var.bnum}"
  alb_name      = "${local.prefix}-alb-${var.bnum}"
  tg_name       = "${local.prefix}-tg-${var.bnum}"
  pool_name     = "${local.prefix}-pool-${var.bnum}"
  alarm_name    = "${local.prefix}-5xx-${var.bnum}"
}

provider "aws" {
  region  = local.region
  profile = "lee"
}

data "aws_caller_identity" "current" {}
data "aws_vpc" "default" { default = true }
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# ─── 자체서명 인증서 (HTTPS 리스너용) ────────────────────────────────────────

resource "tls_private_key" "self_signed" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "self_signed" {
  private_key_pem = tls_private_key.self_signed.private_key_pem

  subject { common_name = "wsc2026-lab.internal" }
  dns_names             = ["wsc2026-lab.internal"]
  validity_period_hours = 8760
  allowed_uses          = ["key_encipherment", "digital_signature", "server_auth"]
}

resource "aws_acm_certificate" "self_signed" {
  private_key      = tls_private_key.self_signed.private_key_pem
  certificate_body = tls_self_signed_cert.self_signed.cert_pem
}

# ─── Cognito ─────────────────────────────────────────────────────────────────

resource "aws_cognito_user_pool" "main" {
  name = local.pool_name

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_numbers   = true
    require_symbols   = false
    require_uppercase = false
  }

  auto_verified_attributes = ["email"]

  schema {
    attribute_data_type = "String"
    name                = "email"
    required            = true
    mutable             = true
  }

  tags = { Project = "wsc2026" }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = "wsc2026-auth-${var.bnum}-${substr(data.aws_caller_identity.current.account_id, 8, 4)}"
  user_pool_id = aws_cognito_user_pool.main.id
}

resource "aws_cognito_user_pool_client" "main" {
  name         = "${local.prefix}-client-${var.bnum}"
  user_pool_id = aws_cognito_user_pool.main.id

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]

  supported_identity_providers = ["COGNITO"]

  callback_urls = ["https://${aws_lb.main.dns_name}/oauth2/idpresponse"]
  logout_urls   = ["https://${aws_lb.main.dns_name}/logout"]

  generate_secret = true
}

# ─── 보안 그룹 ─────────────────────────────────────────────────────────────────

resource "aws_security_group" "alb" {
  name   = "${local.prefix}-alb-sg-${var.bnum}"
  vpc_id = data.aws_vpc.default.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Project = "wsc2026" }
}

resource "aws_security_group" "ecs" {
  name   = "${local.prefix}-ecs-sg-${var.bnum}"
  vpc_id = data.aws_vpc.default.id

  ingress {
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Project = "wsc2026" }
}

# ─── ALB ─────────────────────────────────────────────────────────────────────

resource "aws_lb" "main" {
  name               = local.alb_name
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = data.aws_subnets.default.ids

  tags = { Project = "wsc2026" }
}

resource "aws_lb_target_group" "main" {
  name        = local.tg_name
  port        = 80
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip"

  health_check {
    path                = "/"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
  }

  tags = { Project = "wsc2026" }
}

# HTTP → HTTPS 리다이렉트
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# HTTPS 리스너 (authenticate-cognito)
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.self_signed.arn

  default_action {
    type = "authenticate-cognito"

    authenticate_cognito {
      user_pool_arn       = aws_cognito_user_pool.main.arn
      user_pool_client_id = aws_cognito_user_pool_client.main.id
      user_pool_domain    = aws_cognito_user_pool_domain.main.domain

      on_unauthenticated_request = "authenticate"
      scope                      = "openid email profile"

      session_cookie_name = "AWSELBAuthSessionCookie"
      session_timeout     = 3600
    }

    order = 1
  }

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.main.arn
    order            = 2
  }

  depends_on = [aws_cognito_user_pool_client.main]
}

# ─── ECS ─────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${local.prefix}-${var.bnum}"
  retention_in_days = 7
}

resource "aws_ecs_cluster" "main" {
  name = local.cluster_name
  tags = { Project = "wsc2026" }
}

resource "aws_iam_role" "ecs_exec" {
  name = "${local.prefix}-exec-role-${var.bnum}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_exec" {
  role       = aws_iam_role.ecs_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_ecs_task_definition" "main" {
  family                   = "${local.prefix}-task-${var.bnum}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_exec.arn

  container_definitions = jsonencode([{
    name      = "portal"
    image     = "public.ecr.aws/nginx/nginx:latest"
    essential = true

    portMappings = [{
      containerPort = 80
      protocol      = "tcp"
    }]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = "/ecs/${local.prefix}-${var.bnum}"
        "awslogs-region"        = local.region
        "awslogs-stream-prefix" = "portal"
      }
    }
  }])

  tags = { Project = "wsc2026" }
}

resource "aws_ecs_service" "main" {
  name            = local.service_name
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.main.arn
  desired_count   = 2
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.main.arn
    container_name   = "portal"
    container_port   = 80
  }

  depends_on = [aws_lb_listener.https, aws_cloudwatch_log_group.ecs]

  tags = { Project = "wsc2026" }
}

# ─── CloudWatch Alarm ─────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = local.alarm_name
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "HTTPCode_ELB_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 5

  dimensions = {
    LoadBalancer = aws_lb.main.arn_suffix
  }

  alarm_description  = "ALB 5xx errors"
  treat_missing_data = "notBreaching"
}

# ─── Outputs ──────────────────────────────────────────────────────────────────

output "alb_dns" {
  value = "https://${aws_lb.main.dns_name}"
}

output "cognito_hosted_ui" {
  value = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${local.region}.amazoncognito.com"
}

output "user_pool_id" {
  value = aws_cognito_user_pool.main.id
}
