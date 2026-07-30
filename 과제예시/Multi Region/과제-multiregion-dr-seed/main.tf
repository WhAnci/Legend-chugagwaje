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

# 실제 위임받은 서브도메인(예: 01.gongju.click)을 넣으면 실 도메인으로 동작.
# 비워두면 가짜 검증용 영역(wsc2026-dr-<비번호>.lab)을 사용.
variable "zone_name" {
  description = "Route53 호스팅 영역 이름 (위임받은 서브도메인 또는 공란)"
  default     = ""
}

locals {
  prefix         = "wsc2026-dr"
  region_primary = "ap-northeast-2"
  region_standby = "ap-northeast-1"

  cidr_primary = "10.20.0.0/16"
  cidr_standby = "10.10.0.0/16"

  zone_name   = var.zone_name != "" ? var.zone_name : "${local.prefix}-${var.bnum}.lab"
  record_name = "app.${local.zone_name}"
}

provider "aws" {
  region  = local.region_primary
  profile = "lee"
}

provider "aws" {
  alias   = "standby"
  region  = local.region_standby
  profile = "lee"
}

data "aws_availability_zones" "primary" { state = "available" }
data "aws_availability_zones" "standby" {
  provider = aws.standby
  state    = "available"
}
data "aws_ssm_parameter" "ami_primary" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}
data "aws_ssm_parameter" "ami_standby" {
  provider = aws.standby
  name     = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# ════════════════════════════════════════════════════════════════════════════
#  PRIMARY REGION (서울) — 네트워크
# ════════════════════════════════════════════════════════════════════════════

resource "aws_vpc" "primary" {
  cidr_block           = local.cidr_primary
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.prefix}-vpc-p-${var.bnum}", Project = "wsc2026" }
}

resource "aws_internet_gateway" "primary" {
  vpc_id = aws_vpc.primary.id
  tags   = { Name = "${local.prefix}-igw-p-${var.bnum}", Project = "wsc2026" }
}

resource "aws_subnet" "primary" {
  count                   = 2
  vpc_id                  = aws_vpc.primary.id
  cidr_block              = cidrsubnet(local.cidr_primary, 8, count.index)
  availability_zone       = data.aws_availability_zones.primary.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.prefix}-subnet-p-${count.index}-${var.bnum}", Project = "wsc2026" }
}

resource "aws_route_table" "primary" {
  vpc_id = aws_vpc.primary.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.primary.id
  }
  tags = { Name = "${local.prefix}-rt-p-${var.bnum}", Project = "wsc2026" }
}

resource "aws_route_table_association" "primary" {
  count          = 2
  subnet_id      = aws_subnet.primary[count.index].id
  route_table_id = aws_route_table.primary.id
}

resource "aws_security_group" "alb_primary" {
  name   = "${local.prefix}-alb-sg-${var.bnum}"
  vpc_id = aws_vpc.primary.id
  ingress {
    description = "HTTP"
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

resource "aws_security_group" "app_primary" {
  name   = "${local.prefix}-app-sg-${var.bnum}"
  vpc_id = aws_vpc.primary.id
  ingress {
    description     = "HTTP from ALB"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb_primary.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Project = "wsc2026" }
}

# ─── PRIMARY: 애플리케이션 ─────────────────────────────────────────────────────

resource "aws_launch_template" "primary" {
  name_prefix            = "${local.prefix}-lt-p-${var.bnum}-"
  image_id               = data.aws_ssm_parameter.ami_primary.value
  instance_type          = "t3.micro"
  vpc_security_group_ids = [aws_security_group.app_primary.id]

  user_data = base64encode(<<-EOF
    #!/bin/bash
    dnf install -y httpd
    echo "REGION ap-northeast-2 APP-OK $(hostname)" > /var/www/html/index.html
    echo "OK" > /var/www/html/health
    systemctl enable --now httpd
  EOF
  )

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${local.prefix}-app-primary-${var.bnum}", Project = "wsc2026" }
  }
}

resource "aws_lb" "primary" {
  name               = "${local.prefix}-alb-p-${var.bnum}"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb_primary.id]
  subnets            = aws_subnet.primary[*].id
  tags               = { Project = "wsc2026" }
}

resource "aws_lb_target_group" "primary" {
  name        = "${local.prefix}-tg-p-${var.bnum}"
  port        = 80
  protocol    = "HTTP"
  vpc_id      = aws_vpc.primary.id
  target_type = "instance"
  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 15
    timeout             = 5
    matcher             = "200"
  }
  tags = { Project = "wsc2026" }
}

resource "aws_lb_listener" "primary" {
  load_balancer_arn = aws_lb.primary.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.primary.arn
  }
}

resource "aws_autoscaling_group" "primary" {
  name                      = "${local.prefix}-asg-p-${var.bnum}"
  vpc_zone_identifier       = aws_subnet.primary[*].id
  desired_capacity          = 2
  min_size                  = 2
  max_size                  = 4
  health_check_type         = "ELB"
  health_check_grace_period = 120
  target_group_arns         = [aws_lb_target_group.primary.arn]
  launch_template {
    id      = aws_launch_template.primary.id
    version = "$Latest"
  }
  tag {
    key                 = "Name"
    value               = "${local.prefix}-app-primary-${var.bnum}"
    propagate_at_launch = true
  }
}

# ════════════════════════════════════════════════════════════════════════════
#  STANDBY REGION (도쿄) — 네트워크
# ════════════════════════════════════════════════════════════════════════════

resource "aws_vpc" "standby" {
  provider             = aws.standby
  cidr_block           = local.cidr_standby
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.prefix}-vpc-s-${var.bnum}", Project = "wsc2026" }
}

resource "aws_internet_gateway" "standby" {
  provider = aws.standby
  vpc_id   = aws_vpc.standby.id
  tags     = { Name = "${local.prefix}-igw-s-${var.bnum}", Project = "wsc2026" }
}

resource "aws_subnet" "standby" {
  provider                = aws.standby
  count                   = 2
  vpc_id                  = aws_vpc.standby.id
  cidr_block              = cidrsubnet(local.cidr_standby, 8, count.index)
  availability_zone       = data.aws_availability_zones.standby.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.prefix}-subnet-s-${count.index}-${var.bnum}", Project = "wsc2026" }
}

resource "aws_route_table" "standby" {
  provider = aws.standby
  vpc_id   = aws_vpc.standby.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.standby.id
  }
  tags = { Name = "${local.prefix}-rt-s-${var.bnum}", Project = "wsc2026" }
}

resource "aws_route_table_association" "standby" {
  provider       = aws.standby
  count          = 2
  subnet_id      = aws_subnet.standby[count.index].id
  route_table_id = aws_route_table.standby.id
}

resource "aws_security_group" "alb_standby" {
  provider = aws.standby
  name     = "${local.prefix}-alb-sg-${var.bnum}"
  vpc_id   = aws_vpc.standby.id
  ingress {
    description = "HTTP"
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

resource "aws_security_group" "app_standby" {
  provider = aws.standby
  name     = "${local.prefix}-app-sg-${var.bnum}"
  vpc_id   = aws_vpc.standby.id
  ingress {
    description     = "HTTP from ALB"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb_standby.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Project = "wsc2026" }
}

# ─── STANDBY: 애플리케이션 ─────────────────────────────────────────────────────

resource "aws_launch_template" "standby" {
  provider               = aws.standby
  name_prefix            = "${local.prefix}-lt-s-${var.bnum}-"
  image_id               = data.aws_ssm_parameter.ami_standby.value
  instance_type          = "t3.micro"
  vpc_security_group_ids = [aws_security_group.app_standby.id]

  user_data = base64encode(<<-EOF
    #!/bin/bash
    dnf install -y httpd
    echo "REGION ap-northeast-1 APP-OK $(hostname)" > /var/www/html/index.html
    echo "OK" > /var/www/html/health
    systemctl enable --now httpd
  EOF
  )

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${local.prefix}-app-standby-${var.bnum}", Project = "wsc2026" }
  }
}

resource "aws_lb" "standby" {
  provider           = aws.standby
  name               = "${local.prefix}-alb-s-${var.bnum}"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb_standby.id]
  subnets            = aws_subnet.standby[*].id
  tags               = { Project = "wsc2026" }
}

resource "aws_lb_target_group" "standby" {
  provider    = aws.standby
  name        = "${local.prefix}-tg-s-${var.bnum}"
  port        = 80
  protocol    = "HTTP"
  vpc_id      = aws_vpc.standby.id
  target_type = "instance"
  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 15
    timeout             = 5
    matcher             = "200"
  }
  tags = { Project = "wsc2026" }
}

resource "aws_lb_listener" "standby" {
  provider          = aws.standby
  load_balancer_arn = aws_lb.standby.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.standby.arn
  }
}

resource "aws_autoscaling_group" "standby" {
  provider                  = aws.standby
  name                      = "${local.prefix}-asg-s-${var.bnum}"
  vpc_zone_identifier       = aws_subnet.standby[*].id
  desired_capacity          = 2
  min_size                  = 2
  max_size                  = 4
  health_check_type         = "ELB"
  health_check_grace_period = 120
  target_group_arns         = [aws_lb_target_group.standby.arn]
  launch_template {
    id      = aws_launch_template.standby.id
    version = "$Latest"
  }
  tag {
    key                 = "Name"
    value               = "${local.prefix}-app-standby-${var.bnum}"
    propagate_at_launch = true
  }
}

# ════════════════════════════════════════════════════════════════════════════
#  ROUTE53 (글로벌)
# ════════════════════════════════════════════════════════════════════════════

resource "aws_route53_health_check" "primary" {
  fqdn              = aws_lb.primary.dns_name
  port              = 80
  type              = "HTTP"
  resource_path     = "/health"
  request_interval  = 10
  failure_threshold = 3
  tags              = { Name = "${local.prefix}-hc-${var.bnum}", Project = "wsc2026" }
}

resource "aws_route53_zone" "main" {
  name = local.zone_name
  tags = { Project = "wsc2026" }
}

resource "aws_route53_record" "primary" {
  zone_id        = aws_route53_zone.main.zone_id
  name           = local.record_name
  type           = "A"
  set_identifier = "primary"
  failover_routing_policy {
    type = "PRIMARY"
  }
  health_check_id = aws_route53_health_check.primary.id
  alias {
    name                   = aws_lb.primary.dns_name
    zone_id                = aws_lb.primary.zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "standby" {
  zone_id        = aws_route53_zone.main.zone_id
  name           = local.record_name
  type           = "A"
  set_identifier = "standby"
  failover_routing_policy {
    type = "SECONDARY"
  }
  alias {
    name                   = aws_lb.standby.dns_name
    zone_id                = aws_lb.standby.zone_id
    evaluate_target_health = true
  }
}

# ─── Outputs ──────────────────────────────────────────────────────────────────

output "zone_name"    { value = local.zone_name }
output "record_name"  { value = local.record_name }
output "name_servers" { value = aws_route53_zone.main.name_servers }
output "primary_alb"  { value = "http://${aws_lb.primary.dns_name}" }
output "standby_alb"  { value = "http://${aws_lb.standby.dns_name}" }
output "primary_asg"  { value = aws_autoscaling_group.primary.name }
output "standby_asg"  { value = aws_autoscaling_group.standby.name }
output "app_url"      { value = "http://${local.record_name}" }
