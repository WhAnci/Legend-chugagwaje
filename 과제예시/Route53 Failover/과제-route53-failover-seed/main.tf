terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "bnum" {
  description = "비번호 (예: 01)"
  default     = "99"
}

locals {
  prefix = "wsc2026-fo"
  region = "ap-northeast-2"

  zone_name   = "${local.prefix}-${var.bnum}.lab"
  record_name = "app.${local.prefix}-${var.bnum}.lab"
}

provider "aws" {
  region  = local.region
  profile = "lee"
}

data "aws_vpc" "default" { default = true }

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_subnet" "selected" {
  for_each = toset(slice(data.aws_subnets.default.ids, 0, 2))
  id       = each.value
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# ─── 보안 그룹 ─────────────────────────────────────────────────────────────────

resource "aws_security_group" "web" {
  name   = "${local.prefix}-sg-${var.bnum}"
  vpc_id = data.aws_vpc.default.id

  ingress {
    description = "HTTP from anywhere (health check + grading)"
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

# ─── EC2 (Primary / Secondary) ────────────────────────────────────────────────

locals {
  subnet_ids = slice(data.aws_subnets.default.ids, 0, 2)
}

resource "aws_instance" "primary" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = "t3.micro"
  subnet_id              = local.subnet_ids[0]
  vpc_security_group_ids = [aws_security_group.web.id]

  user_data = <<-EOF
    #!/bin/bash
    dnf install -y httpd
    echo "PRIMARY" > /var/www/html/index.html
    systemctl enable --now httpd
  EOF

  tags = {
    Name    = "${local.prefix}-primary-${var.bnum}"
    Role    = "primary"
    Project = "wsc2026"
  }
}

resource "aws_instance" "secondary" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = "t3.micro"
  subnet_id              = local.subnet_ids[1]
  vpc_security_group_ids = [aws_security_group.web.id]

  user_data = <<-EOF
    #!/bin/bash
    dnf install -y httpd
    echo "SECONDARY" > /var/www/html/index.html
    systemctl enable --now httpd
  EOF

  tags = {
    Name    = "${local.prefix}-secondary-${var.bnum}"
    Role    = "secondary"
    Project = "wsc2026"
  }
}

# ─── EIP (stop/start 시에도 IP 고정) ──────────────────────────────────────────

resource "aws_eip" "primary" {
  instance = aws_instance.primary.id
  domain   = "vpc"
  tags     = { Name = "${local.prefix}-eip-primary-${var.bnum}" }
}

resource "aws_eip" "secondary" {
  instance = aws_instance.secondary.id
  domain   = "vpc"
  tags     = { Name = "${local.prefix}-eip-secondary-${var.bnum}" }
}

# ─── Route53 헬스체크 ──────────────────────────────────────────────────────────

resource "aws_route53_health_check" "primary" {
  ip_address        = aws_eip.primary.public_ip
  port              = 80
  type              = "HTTP"
  resource_path     = "/"
  request_interval  = 10
  failure_threshold = 3

  tags = {
    Name    = "${local.prefix}-hc-primary-${var.bnum}"
    Project = "wsc2026"
  }
}

# ─── Route53 호스팅 영역 + 페일오버 레코드 ──────────────────────────────────────

resource "aws_route53_zone" "main" {
  name = local.zone_name
  tags = { Project = "wsc2026" }
}

resource "aws_route53_record" "primary" {
  zone_id        = aws_route53_zone.main.zone_id
  name           = local.record_name
  type           = "A"
  ttl            = 10
  set_identifier = "primary"

  failover_routing_policy {
    type = "PRIMARY"
  }

  health_check_id = aws_route53_health_check.primary.id
  records         = [aws_eip.primary.public_ip]
}

resource "aws_route53_record" "secondary" {
  zone_id        = aws_route53_zone.main.zone_id
  name           = local.record_name
  type           = "A"
  ttl            = 10
  set_identifier = "secondary"

  failover_routing_policy {
    type = "SECONDARY"
  }

  records = [aws_eip.secondary.public_ip]
}

# ─── Outputs ──────────────────────────────────────────────────────────────────

output "zone_name" {
  value = local.zone_name
}

output "record_name" {
  value = local.record_name
}

output "name_servers" {
  value = aws_route53_zone.main.name_servers
}

output "primary_ip" {
  value = aws_eip.primary.public_ip
}

output "secondary_ip" {
  value = aws_eip.secondary.public_ip
}
