# VPC layout (2 AZs):
#   public   - ALB + NAT gateway(s); routes to the internet gateway
#   private  - ECS Fargate tasks; egress via NAT, AWS APIs via VPC endpoints
#   isolated - RDS only; no route to the internet at all
#
# FedLine / sponsor-bank connectivity attaches here: either
#   * an AWS Site-to-Site VPN (aws_vpn_gateway / aws_customer_gateway / aws_vpn_connection) or a
#     Transit Gateway attachment, with routes to the bank's prefixes added to the PRIVATE route tables, or
#   * an AWS PrivateLink interface endpoint (aws_vpc_endpoint, type Interface) to a service the sponsor
#     bank exposes, placed in the private subnets with its own security group.
# Only the ECS backend/scheduler security group should be allowed to reach that connection.

locals {
  public_subnets   = [for i, az in local.azs : cidrsubnet(var.vpc_cidr, 8, i)]
  private_subnets  = [for i, az in local.azs : cidrsubnet(var.vpc_cidr, 4, i + 1)]
  isolated_subnets = [for i, az in local.azs : cidrsubnet(var.vpc_cidr, 8, i + 100)]
  nat_count        = var.nat_gateway_per_az ? length(local.azs) : 1
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = local.name }
}

# Lock down the default security group so nothing can accidentally use it.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${local.name}-default-deny" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = local.name }
}

# ---------------------------------------------------------------- subnets
resource "aws_subnet" "public" {
  count                   = length(local.azs)
  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.azs[count.index]
  cidr_block              = local.public_subnets[count.index]
  map_public_ip_on_launch = false

  tags = { Name = "${local.name}-public-${local.azs[count.index]}", Tier = "public" }
}

resource "aws_subnet" "private" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = local.private_subnets[count.index]

  tags = { Name = "${local.name}-private-${local.azs[count.index]}", Tier = "private" }
}

resource "aws_subnet" "isolated" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = local.isolated_subnets[count.index]

  tags = { Name = "${local.name}-isolated-${local.azs[count.index]}", Tier = "isolated" }
}

# ---------------------------------------------------------------- NAT
resource "aws_eip" "nat" {
  count  = local.nat_count
  domain = "vpc"
  tags   = { Name = "${local.name}-nat-${count.index}" }
}

resource "aws_nat_gateway" "this" {
  count         = local.nat_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = { Name = "${local.name}-nat-${count.index}" }

  depends_on = [aws_internet_gateway.this]
}

# ---------------------------------------------------------------- routing
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${local.name}-public" }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count  = length(local.azs)
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${local.name}-private-${local.azs[count.index]}" }
}

resource "aws_route" "private_nat" {
  count                  = length(local.azs)
  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[var.nat_gateway_per_az ? count.index : 0].id
}

# Sponsor bank / FedLine routes (VPN or TGW) would be added to aws_route_table.private here.

resource "aws_route_table_association" "private" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# Isolated tier: local VPC routing only (no 0.0.0.0/0 route).
resource "aws_route_table" "isolated" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${local.name}-isolated" }
}

resource "aws_route_table_association" "isolated" {
  count          = length(local.azs)
  subnet_id      = aws_subnet.isolated[count.index].id
  route_table_id = aws_route_table.isolated.id
}

# ---------------------------------------------------------------- VPC endpoints
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat(aws_route_table.private[*].id, [aws_route_table.isolated.id])

  tags = { Name = "${local.name}-s3" }
}

locals {
  interface_endpoints = {
    ecr_api        = "ecr.api"
    ecr_dkr        = "ecr.dkr"
    secretsmanager = "secretsmanager"
    logs           = "logs"
  }
}

resource "aws_vpc_endpoint" "interface" {
  for_each = local.interface_endpoints

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]

  tags = { Name = "${local.name}-${each.key}" }
}

# ---------------------------------------------------------------- flow logs (audit)
resource "aws_cloudwatch_log_group" "flow_logs" {
  name              = "/aws/vpc/${local.name}/flow-logs"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
}

data "aws_iam_policy_document" "flow_logs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "flow_logs" {
  name               = "${local.name}-vpc-flow-logs"
  assume_role_policy = data.aws_iam_policy_document.flow_logs_assume.json
}

data "aws_iam_policy_document" "flow_logs" {
  statement {
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]
    resources = ["${aws_cloudwatch_log_group.flow_logs.arn}:*"]
  }
}

resource "aws_iam_role_policy" "flow_logs" {
  name   = "write-flow-logs"
  role   = aws_iam_role.flow_logs.id
  policy = data.aws_iam_policy_document.flow_logs.json
}

resource "aws_flow_log" "this" {
  vpc_id               = aws_vpc.this.id
  traffic_type         = "ALL"
  log_destination_type = "cloud-watch-logs"
  log_destination      = aws_cloudwatch_log_group.flow_logs.arn
  iam_role_arn         = aws_iam_role.flow_logs.arn
}
