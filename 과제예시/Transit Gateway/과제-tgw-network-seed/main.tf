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
  prefix = "wsc2026-net"
  region = "ap-northeast-3"

  cidr_egress = "10.0.0.0/16"
  cidr_app    = "10.1.0.0/16"
  cidr_db     = "10.2.0.0/16"
}

provider "aws" {
  region  = local.region
  profile = "lee"
}

data "aws_availability_zones" "azs" { state = "available" }
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  az = data.aws_availability_zones.azs.names[0]
}

# ════════════════════════════════════════════════════════════════════════════
#  IAM (SSM 공통)
# ════════════════════════════════════════════════════════════════════════════

resource "aws_iam_role" "ssm" {
  name = "${local.prefix}-ssm-role-${var.bnum}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ssm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_instance_profile" "ssm" {
  name = "${local.prefix}-ssm-profile-${var.bnum}"
  role = aws_iam_role.ssm.name
}

# ════════════════════════════════════════════════════════════════════════════
#  EGRESS VPC (10.0.0.0/16) — 중앙집중 인터넷 출구 (IGW + NAT)
# ════════════════════════════════════════════════════════════════════════════

resource "aws_vpc" "egress" {
  cidr_block           = local.cidr_egress
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.prefix}-vpc-egress-${var.bnum}", Tier = "egress", Project = "wsc2026" }
}

resource "aws_internet_gateway" "egress" {
  vpc_id = aws_vpc.egress.id
  tags   = { Name = "${local.prefix}-igw-${var.bnum}", Project = "wsc2026" }
}

resource "aws_subnet" "egress_public" {
  vpc_id                  = aws_vpc.egress.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = local.az
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.prefix}-egress-public-${var.bnum}", Project = "wsc2026" }
}

resource "aws_subnet" "egress_tgw" {
  vpc_id            = aws_vpc.egress.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = local.az
  tags              = { Name = "${local.prefix}-egress-tgw-${var.bnum}", Project = "wsc2026" }
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${local.prefix}-nat-eip-${var.bnum}", Project = "wsc2026" }
}

resource "aws_nat_gateway" "egress" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.egress_public.id
  tags          = { Name = "${local.prefix}-nat-${var.bnum}", Project = "wsc2026" }
  depends_on    = [aws_internet_gateway.egress]
}

# Egress 퍼블릭 서브넷 RT: 인터넷은 IGW, 스포크 반환은 TGW
resource "aws_route_table" "egress_public" {
  vpc_id = aws_vpc.egress.id
  tags   = { Name = "${local.prefix}-rt-egress-public-${var.bnum}", Project = "wsc2026" }
}
resource "aws_route" "egress_public_inet" {
  route_table_id         = aws_route_table.egress_public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.egress.id
}
resource "aws_route" "egress_public_to_app" {
  route_table_id         = aws_route_table.egress_public.id
  destination_cidr_block = local.cidr_app
  transit_gateway_id     = aws_ec2_transit_gateway.main.id
  depends_on             = [aws_ec2_transit_gateway_vpc_attachment.egress]
}
resource "aws_route" "egress_public_to_db" {
  route_table_id         = aws_route_table.egress_public.id
  destination_cidr_block = local.cidr_db
  transit_gateway_id     = aws_ec2_transit_gateway.main.id
  depends_on             = [aws_ec2_transit_gateway_vpc_attachment.egress]
}
resource "aws_route_table_association" "egress_public" {
  subnet_id      = aws_subnet.egress_public.id
  route_table_id = aws_route_table.egress_public.id
}

# Egress TGW 서브넷 RT: 스포크에서 온 인터넷행 트래픽을 NAT로
resource "aws_route_table" "egress_tgw" {
  vpc_id = aws_vpc.egress.id
  tags   = { Name = "${local.prefix}-rt-egress-tgw-${var.bnum}", Project = "wsc2026" }
}
resource "aws_route" "egress_tgw_to_nat" {
  route_table_id         = aws_route_table.egress_tgw.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.egress.id
}
resource "aws_route_table_association" "egress_tgw" {
  subnet_id      = aws_subnet.egress_tgw.id
  route_table_id = aws_route_table.egress_tgw.id
}

# ════════════════════════════════════════════════════════════════════════════
#  APP VPC (10.1.0.0/16) — private, 인터넷은 TGW→Egress NAT 경유
# ════════════════════════════════════════════════════════════════════════════

resource "aws_vpc" "app" {
  cidr_block           = local.cidr_app
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.prefix}-vpc-app-${var.bnum}", Tier = "app", Project = "wsc2026" }
}

resource "aws_subnet" "app" {
  vpc_id            = aws_vpc.app.id
  cidr_block        = "10.1.1.0/24"
  availability_zone = local.az
  tags              = { Name = "${local.prefix}-app-${var.bnum}", Project = "wsc2026" }
}

resource "aws_route_table" "app" {
  vpc_id = aws_vpc.app.id
  tags   = { Name = "${local.prefix}-rt-app-${var.bnum}", Project = "wsc2026" }
}
resource "aws_route" "app_default_to_tgw" {
  route_table_id         = aws_route_table.app.id
  destination_cidr_block = "0.0.0.0/0"
  transit_gateway_id     = aws_ec2_transit_gateway.main.id
  depends_on             = [aws_ec2_transit_gateway_vpc_attachment.app]
}
resource "aws_route" "app_to_db" {
  route_table_id         = aws_route_table.app.id
  destination_cidr_block = local.cidr_db
  transit_gateway_id     = aws_ec2_transit_gateway.main.id
  depends_on             = [aws_ec2_transit_gateway_vpc_attachment.app]
}
resource "aws_route_table_association" "app" {
  subnet_id      = aws_subnet.app.id
  route_table_id = aws_route_table.app.id
}

# ════════════════════════════════════════════════════════════════════════════
#  DB VPC (10.2.0.0/16) — private, 인터넷 차단 (App 만 통신)
# ════════════════════════════════════════════════════════════════════════════

resource "aws_vpc" "db" {
  cidr_block           = local.cidr_db
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.prefix}-vpc-db-${var.bnum}", Tier = "db", Project = "wsc2026" }
}

resource "aws_subnet" "db" {
  vpc_id            = aws_vpc.db.id
  cidr_block        = "10.2.1.0/24"
  availability_zone = local.az
  tags              = { Name = "${local.prefix}-db-${var.bnum}", Project = "wsc2026" }
}

resource "aws_route_table" "db" {
  vpc_id = aws_vpc.db.id
  tags   = { Name = "${local.prefix}-rt-db-${var.bnum}", Project = "wsc2026" }
}
# DB 는 App(10.1) 으로만 라우팅. 기본 라우트(0.0.0.0/0) 없음 → 인터넷 차단
resource "aws_route" "db_to_app" {
  route_table_id         = aws_route_table.db.id
  destination_cidr_block = local.cidr_app
  transit_gateway_id     = aws_ec2_transit_gateway.main.id
  depends_on             = [aws_ec2_transit_gateway_vpc_attachment.db]
}
resource "aws_route_table_association" "db" {
  subnet_id      = aws_subnet.db.id
  route_table_id = aws_route_table.db.id
}

# ════════════════════════════════════════════════════════════════════════════
#  Security Groups
# ════════════════════════════════════════════════════════════════════════════

resource "aws_security_group" "app_instance" {
  name   = "${local.prefix}-app-sg-${var.bnum}"
  vpc_id = aws_vpc.app.id
  ingress {
    description = "ICMP+HTTP from internal"
    from_port   = -1
    to_port     = -1
    protocol    = "icmp"
    cidr_blocks = ["10.0.0.0/8"]
  }
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Project = "wsc2026" }
}

resource "aws_security_group" "db_instance" {
  name   = "${local.prefix}-db-sg-${var.bnum}"
  vpc_id = aws_vpc.db.id
  ingress {
    description = "ICMP+HTTP from internal"
    from_port   = -1
    to_port     = -1
    protocol    = "icmp"
    cidr_blocks = ["10.0.0.0/8"]
  }
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Project = "wsc2026" }
}

# SSM 엔드포인트용 SG (443 from VPC)
resource "aws_security_group" "vpce_app" {
  name   = "${local.prefix}-vpce-app-sg-${var.bnum}"
  vpc_id = aws_vpc.app.id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [local.cidr_app]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Project = "wsc2026" }
}
resource "aws_security_group" "vpce_db" {
  name   = "${local.prefix}-vpce-db-sg-${var.bnum}"
  vpc_id = aws_vpc.db.id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [local.cidr_db]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Project = "wsc2026" }
}

# ════════════════════════════════════════════════════════════════════════════
#  SSM Interface Endpoints (private 인스턴스 채점 접근용)
# ════════════════════════════════════════════════════════════════════════════

locals {
  ssm_services = ["ssm", "ssmmessages", "ec2messages"]
}

resource "aws_vpc_endpoint" "app_ssm" {
  for_each            = toset(local.ssm_services)
  vpc_id              = aws_vpc.app.id
  service_name        = "com.amazonaws.${local.region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.app.id]
  security_group_ids  = [aws_security_group.vpce_app.id]
  private_dns_enabled = true
  tags                = { Name = "${local.prefix}-app-${each.value}-${var.bnum}", Project = "wsc2026" }
}

resource "aws_vpc_endpoint" "db_ssm" {
  for_each            = toset(local.ssm_services)
  vpc_id              = aws_vpc.db.id
  service_name        = "com.amazonaws.${local.region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.db.id]
  security_group_ids  = [aws_security_group.vpce_db.id]
  private_dns_enabled = true
  tags                = { Name = "${local.prefix}-db-${each.value}-${var.bnum}", Project = "wsc2026" }
}

# ════════════════════════════════════════════════════════════════════════════
#  EC2 (App / DB)
# ════════════════════════════════════════════════════════════════════════════

resource "aws_instance" "app" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = "t3.micro"
  subnet_id              = aws_subnet.app.id
  vpc_security_group_ids = [aws_security_group.app_instance.id]
  iam_instance_profile   = aws_iam_instance_profile.ssm.name
  user_data = base64encode(<<-EOF
    #!/bin/bash
    dnf install -y httpd
    echo "APP-OK $(hostname)" > /var/www/html/index.html
    systemctl enable --now httpd
  EOF
  )
  tags = { Name = "${local.prefix}-app-ec2-${var.bnum}", Tier = "app", Project = "wsc2026" }
}

resource "aws_instance" "db" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = "t3.micro"
  subnet_id              = aws_subnet.db.id
  vpc_security_group_ids = [aws_security_group.db_instance.id]
  iam_instance_profile   = aws_iam_instance_profile.ssm.name
  # DB VPC는 인터넷이 없어 패키지 설치 불가 → 기본 내장 python http.server 사용
  user_data_replace_on_change = true
  user_data = base64encode(<<-EOF
    #!/bin/bash
    mkdir -p /var/www
    echo "DB-OK $(hostname)" > /var/www/index.html
    nohup /usr/bin/python3 -m http.server 80 --directory /var/www >/dev/null 2>&1 &
  EOF
  )
  tags = { Name = "${local.prefix}-db-ec2-${var.bnum}", Tier = "db", Project = "wsc2026" }
}

# ════════════════════════════════════════════════════════════════════════════
#  Transit Gateway + Attachments + Route Tables (세그멘테이션)
# ════════════════════════════════════════════════════════════════════════════

resource "aws_ec2_transit_gateway" "main" {
  description                     = "${local.prefix}-tgw-${var.bnum}"
  default_route_table_association = "disable"
  default_route_table_propagation = "disable"
  tags                            = { Name = "${local.prefix}-tgw-${var.bnum}", Project = "wsc2026" }
}

resource "aws_ec2_transit_gateway_vpc_attachment" "egress" {
  transit_gateway_id                              = aws_ec2_transit_gateway.main.id
  vpc_id                                          = aws_vpc.egress.id
  subnet_ids                                      = [aws_subnet.egress_tgw.id]
  transit_gateway_default_route_table_association = false
  transit_gateway_default_route_table_propagation = false
  tags                                            = { Name = "${local.prefix}-att-egress-${var.bnum}", Project = "wsc2026" }
}
resource "aws_ec2_transit_gateway_vpc_attachment" "app" {
  transit_gateway_id                              = aws_ec2_transit_gateway.main.id
  vpc_id                                          = aws_vpc.app.id
  subnet_ids                                      = [aws_subnet.app.id]
  transit_gateway_default_route_table_association = false
  transit_gateway_default_route_table_propagation = false
  tags                                            = { Name = "${local.prefix}-att-app-${var.bnum}", Project = "wsc2026" }
}
resource "aws_ec2_transit_gateway_vpc_attachment" "db" {
  transit_gateway_id                              = aws_ec2_transit_gateway.main.id
  vpc_id                                          = aws_vpc.db.id
  subnet_ids                                      = [aws_subnet.db.id]
  transit_gateway_default_route_table_association = false
  transit_gateway_default_route_table_propagation = false
  tags                                            = { Name = "${local.prefix}-att-db-${var.bnum}", Project = "wsc2026" }
}

# --- TGW Route Table: APP (0/0→egress, 10.2→db) ---
resource "aws_ec2_transit_gateway_route_table" "app" {
  transit_gateway_id = aws_ec2_transit_gateway.main.id
  tags               = { Name = "${local.prefix}-tgwrt-app-${var.bnum}", Project = "wsc2026" }
}
resource "aws_ec2_transit_gateway_route_table_association" "app" {
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.app.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.app.id
}
resource "aws_ec2_transit_gateway_route" "app_default" {
  destination_cidr_block         = "0.0.0.0/0"
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.egress.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.app.id
}
resource "aws_ec2_transit_gateway_route" "app_to_db" {
  destination_cidr_block         = local.cidr_db
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.db.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.app.id
}

# --- TGW Route Table: DB (10.1→app 만, 인터넷 없음) ---
resource "aws_ec2_transit_gateway_route_table" "db" {
  transit_gateway_id = aws_ec2_transit_gateway.main.id
  tags               = { Name = "${local.prefix}-tgwrt-db-${var.bnum}", Project = "wsc2026" }
}
resource "aws_ec2_transit_gateway_route_table_association" "db" {
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.db.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.db.id
}
resource "aws_ec2_transit_gateway_route" "db_to_app" {
  destination_cidr_block         = local.cidr_app
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.app.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.db.id
}

# --- TGW Route Table: EGRESS (스포크 반환 라우팅) ---
resource "aws_ec2_transit_gateway_route_table" "egress" {
  transit_gateway_id = aws_ec2_transit_gateway.main.id
  tags               = { Name = "${local.prefix}-tgwrt-egress-${var.bnum}", Project = "wsc2026" }
}
resource "aws_ec2_transit_gateway_route_table_association" "egress" {
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.egress.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.egress.id
}
resource "aws_ec2_transit_gateway_route" "egress_to_app" {
  destination_cidr_block         = local.cidr_app
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.app.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.egress.id
}
resource "aws_ec2_transit_gateway_route" "egress_to_db" {
  destination_cidr_block         = local.cidr_db
  transit_gateway_attachment_id  = aws_ec2_transit_gateway_vpc_attachment.db.id
  transit_gateway_route_table_id = aws_ec2_transit_gateway_route_table.egress.id
}

# ─── Outputs ──────────────────────────────────────────────────────────────────

output "app_instance_id" { value = aws_instance.app.id }
output "db_instance_id"  { value = aws_instance.db.id }
output "app_private_ip"  { value = aws_instance.app.private_ip }
output "db_private_ip"   { value = aws_instance.db.private_ip }
output "tgw_id"          { value = aws_ec2_transit_gateway.main.id }
output "nat_eip"         { value = aws_eip.nat.public_ip }
