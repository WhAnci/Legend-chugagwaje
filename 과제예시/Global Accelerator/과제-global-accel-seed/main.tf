##############################################
# 제6과제 정답 인프라 (출제자 검증용 · 비공개)
# 서울 + 오레곤 2리전 + Global Accelerator
##############################################

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "profile" { default = "lee" }
variable "num" { default = "01" } # 비번호
variable "region_a" { default = "ap-northeast-2" }
variable "region_b" { default = "us-west-2" }

provider "aws" {
  alias   = "seoul"
  region  = var.region_a
  profile = var.profile
}

provider "aws" {
  alias   = "oregon"
  region  = var.region_b
  profile = var.profile
}

# Global Accelerator 는 글로벌 서비스이며 API 엔드포인트가 us-west-2 에만 있다.
provider "aws" {
  alias   = "ga"
  region  = "us-west-2"
  profile = var.profile
}

data "aws_availability_zones" "seoul" {
  provider = aws.seoul
  state    = "available"
}

data "aws_availability_zones" "oregon" {
  provider = aws.oregon
  state    = "available"
}

data "aws_ami" "al2023_seoul" {
  provider    = aws.seoul
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

data "aws_ami" "al2023_oregon" {
  provider    = aws.oregon
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }
}

# arena 바이너리는 user_data 16KB 제한을 넘으므로 S3 에 올려 인스턴스 역할로 내려받는다.
resource "aws_s3_bucket" "assets" {
  provider      = aws.seoul
  bucket        = "wsc2026-ga-assets-${var.num}-${data.aws_caller_identity.me.account_id}"
  force_destroy = true
}

resource "aws_s3_object" "arena" {
  provider = aws.seoul
  bucket   = aws_s3_bucket.assets.id
  key      = "arena"
  source   = "${path.module}/../과제-global-accel-지급파일/arena"
  etag     = filemd5("${path.module}/../과제-global-accel-지급파일/arena")
}

data "aws_caller_identity" "me" {
  provider = aws.seoul
}

locals {
  user_data = <<-EOT
    #!/bin/bash
    set -e
    for i in $(seq 1 30); do
      aws s3 cp s3://${aws_s3_bucket.assets.id}/arena /usr/local/bin/arena --region ${var.region_a} && break
      sleep 5
    done
    chmod +x /usr/local/bin/arena
    cat >/etc/systemd/system/arena.service <<'UNIT'
    [Unit]
    Description=arena matchmaking node
    After=network-online.target
    [Service]
    ExecStart=/usr/local/bin/arena
    Restart=always
    [Install]
    WantedBy=multi-user.target
    UNIT
    systemctl daemon-reload
    systemctl enable --now arena
  EOT
}

##############################################
# IAM — SSM (계정 전역, 한 번만)
##############################################

resource "aws_iam_role" "node" {
  provider = aws.seoul
  name     = "wsc2026-ga-node-role-${var.num}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "node_ssm" {
  provider   = aws.seoul
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "node_assets" {
  provider = aws.seoul
  name     = "assets-read"
  role     = aws_iam_role.node.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "${aws_s3_bucket.assets.arn}/*"
    }]
  })
}

resource "aws_iam_instance_profile" "node" {
  provider = aws.seoul
  name     = "wsc2026-ga-node-profile-${var.num}"
  role     = aws_iam_role.node.name
}

##############################################
# 서울 리전
##############################################

resource "aws_vpc" "seoul" {
  provider             = aws.seoul
  cidr_block           = "10.10.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "wsc2026-ga-vpc-seoul-${var.num}" }
}

resource "aws_internet_gateway" "seoul" {
  provider = aws.seoul
  vpc_id   = aws_vpc.seoul.id
  tags     = { Name = "wsc2026-ga-igw-seoul-${var.num}" }
}

resource "aws_subnet" "seoul" {
  provider                = aws.seoul
  count                   = 2
  vpc_id                  = aws_vpc.seoul.id
  cidr_block              = cidrsubnet("10.10.0.0/16", 8, count.index)
  availability_zone       = data.aws_availability_zones.seoul.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "wsc2026-ga-pub-seoul-${count.index}-${var.num}" }
}

resource "aws_route_table" "seoul" {
  provider = aws.seoul
  vpc_id   = aws_vpc.seoul.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.seoul.id
  }
  tags = { Name = "wsc2026-ga-rt-seoul-${var.num}" }
}

resource "aws_route_table_association" "seoul" {
  provider       = aws.seoul
  count          = 2
  subnet_id      = aws_subnet.seoul[count.index].id
  route_table_id = aws_route_table.seoul.id
}

# 노드 SG — 8080 은 VPC 내부(NLB)에서만. 인터넷에 열지 않는다.
resource "aws_security_group" "node_seoul" {
  provider    = aws.seoul
  name        = "wsc2026-ga-node-sg-seoul-${var.num}"
  description = "arena node - 8080 from VPC only"
  vpc_id      = aws_vpc.seoul.id

  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.seoul.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "wsc2026-ga-node-sg-seoul-${var.num}" }
}

resource "aws_instance" "seoul" {
  provider                    = aws.seoul
  ami                         = data.aws_ami.al2023_seoul.id
  instance_type               = "t3.micro"
  subnet_id                   = aws_subnet.seoul[0].id
  vpc_security_group_ids      = [aws_security_group.node_seoul.id]
  iam_instance_profile        = aws_iam_instance_profile.node.name
  user_data                   = local.user_data
  user_data_replace_on_change = true
  tags                        = { Name = "wsc2026-ga-node-seoul-${var.num}" }
}

resource "aws_lb" "seoul" {
  provider           = aws.seoul
  name               = "wsc2026-ga-nlb-seoul-${var.num}"
  internal           = false
  load_balancer_type = "network"
  subnets            = aws_subnet.seoul[*].id
  tags               = { Name = "wsc2026-ga-nlb-seoul-${var.num}" }
}

resource "aws_lb_target_group" "seoul" {
  provider    = aws.seoul
  name        = "wsc2026-ga-tg-seoul-${var.num}"
  port        = 8080
  protocol    = "TCP"
  target_type = "instance"
  vpc_id      = aws_vpc.seoul.id

  # NLB 가 클라이언트 IP 를 보존하지 않게 하여, 노드 SG 를 VPC 내부로만 열어 둘 수 있게 한다.
  preserve_client_ip = false

  health_check {
    protocol            = "HTTP"
    path                = "/health"
    interval            = 10
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}

resource "aws_lb_target_group_attachment" "seoul" {
  provider         = aws.seoul
  target_group_arn = aws_lb_target_group.seoul.arn
  target_id        = aws_instance.seoul.id
  port             = 8080
}

resource "aws_lb_listener" "seoul" {
  provider          = aws.seoul
  load_balancer_arn = aws_lb.seoul.arn
  port              = 8080
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.seoul.arn
  }
}

##############################################
# 오레곤 리전
##############################################

resource "aws_vpc" "oregon" {
  provider             = aws.oregon
  cidr_block           = "10.20.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "wsc2026-ga-vpc-oregon-${var.num}" }
}

resource "aws_internet_gateway" "oregon" {
  provider = aws.oregon
  vpc_id   = aws_vpc.oregon.id
  tags     = { Name = "wsc2026-ga-igw-oregon-${var.num}" }
}

resource "aws_subnet" "oregon" {
  provider                = aws.oregon
  count                   = 2
  vpc_id                  = aws_vpc.oregon.id
  cidr_block              = cidrsubnet("10.20.0.0/16", 8, count.index)
  availability_zone       = data.aws_availability_zones.oregon.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "wsc2026-ga-pub-oregon-${count.index}-${var.num}" }
}

resource "aws_route_table" "oregon" {
  provider = aws.oregon
  vpc_id   = aws_vpc.oregon.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.oregon.id
  }
  tags = { Name = "wsc2026-ga-rt-oregon-${var.num}" }
}

resource "aws_route_table_association" "oregon" {
  provider       = aws.oregon
  count          = 2
  subnet_id      = aws_subnet.oregon[count.index].id
  route_table_id = aws_route_table.oregon.id
}

resource "aws_security_group" "node_oregon" {
  provider    = aws.oregon
  name        = "wsc2026-ga-node-sg-oregon-${var.num}"
  description = "arena node - 8080 from VPC only"
  vpc_id      = aws_vpc.oregon.id

  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.oregon.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "wsc2026-ga-node-sg-oregon-${var.num}" }
}

resource "aws_instance" "oregon" {
  provider                    = aws.oregon
  ami                         = data.aws_ami.al2023_oregon.id
  instance_type               = "t3.micro"
  subnet_id                   = aws_subnet.oregon[0].id
  vpc_security_group_ids      = [aws_security_group.node_oregon.id]
  iam_instance_profile        = aws_iam_instance_profile.node.name
  user_data                   = local.user_data
  user_data_replace_on_change = true
  tags                        = { Name = "wsc2026-ga-node-oregon-${var.num}" }
}

resource "aws_lb" "oregon" {
  provider           = aws.oregon
  name               = "wsc2026-ga-nlb-oregon-${var.num}"
  internal           = false
  load_balancer_type = "network"
  subnets            = aws_subnet.oregon[*].id
  tags               = { Name = "wsc2026-ga-nlb-oregon-${var.num}" }
}

resource "aws_lb_target_group" "oregon" {
  provider           = aws.oregon
  name               = "wsc2026-ga-tg-oregon-${var.num}"
  port               = 8080
  protocol           = "TCP"
  target_type        = "instance"
  vpc_id             = aws_vpc.oregon.id
  preserve_client_ip = false

  health_check {
    protocol            = "HTTP"
    path                = "/health"
    interval            = 10
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}

resource "aws_lb_target_group_attachment" "oregon" {
  provider         = aws.oregon
  target_group_arn = aws_lb_target_group.oregon.arn
  target_id        = aws_instance.oregon.id
  port             = 8080
}

resource "aws_lb_listener" "oregon" {
  provider          = aws.oregon
  load_balancer_arn = aws_lb.oregon.arn
  port              = 8080
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.oregon.arn
  }
}

##############################################
# Global Accelerator (us-west-2 에서 관리)
##############################################

resource "aws_globalaccelerator_accelerator" "this" {
  provider        = aws.ga
  name            = "wsc2026-ga-${var.num}"
  ip_address_type = "IPV4"
  enabled         = true
}

resource "aws_globalaccelerator_listener" "this" {
  provider        = aws.ga
  accelerator_arn = aws_globalaccelerator_accelerator.this.id
  client_affinity = "NONE"
  protocol        = "TCP"

  port_range {
    from_port = 8080
    to_port   = 8080
  }
}

resource "aws_globalaccelerator_endpoint_group" "seoul" {
  provider                      = aws.ga
  listener_arn                  = aws_globalaccelerator_listener.this.id
  endpoint_group_region         = var.region_a
  traffic_dial_percentage       = 100
  health_check_interval_seconds = 10
  threshold_count               = 3

  endpoint_configuration {
    endpoint_id = aws_lb.seoul.arn
    weight      = 128
  }
}

resource "aws_globalaccelerator_endpoint_group" "oregon" {
  provider                      = aws.ga
  listener_arn                  = aws_globalaccelerator_listener.this.id
  endpoint_group_region         = var.region_b
  traffic_dial_percentage       = 100
  health_check_interval_seconds = 10
  threshold_count               = 3

  endpoint_configuration {
    endpoint_id = aws_lb.oregon.arn
    weight      = 128
  }
}

output "static_ips" {
  value = aws_globalaccelerator_accelerator.this.ip_sets[0].ip_addresses
}

output "seoul_nlb" { value = aws_lb.seoul.dns_name }
output "oregon_nlb" { value = aws_lb.oregon.dns_name }
