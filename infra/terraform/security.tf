# Traffic flow:  internet -443-> ALB -> frontend tasks (80) / backend tasks (8000)
#                frontend tasks -8000-> backend tasks (nginx /api proxy over Service Connect)
#                backend + scheduler tasks -5432-> RDS
# Security group rules use the standalone aws_vpc_security_group_*_rule resources (one rule each).

# ---------------------------------------------------------------- ALB
resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public ALB: HTTPS (and HTTP for redirect) from the internet"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${local.name}-alb" }
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from internet"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_ingress_rule" "alb_http_redirect" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from internet (redirected to HTTPS only)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "alb_to_frontend" {
  security_group_id            = aws_security_group.alb.id
  description                  = "To frontend tasks"
  referenced_security_group_id = aws_security_group.frontend.id
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
}

resource "aws_vpc_security_group_egress_rule" "alb_to_backend" {
  security_group_id            = aws_security_group.alb.id
  description                  = "To backend tasks"
  referenced_security_group_id = aws_security_group.backend.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

# ---------------------------------------------------------------- frontend tasks
resource "aws_security_group" "frontend" {
  name        = "${local.name}-frontend"
  description = "Frontend (nginx) ECS tasks: inbound only from the ALB"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${local.name}-frontend" }
}

resource "aws_vpc_security_group_ingress_rule" "frontend_from_alb" {
  security_group_id            = aws_security_group.frontend.id
  description                  = "HTTP from ALB"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
}

resource "aws_vpc_security_group_egress_rule" "frontend_to_backend" {
  security_group_id            = aws_security_group.frontend.id
  description                  = "nginx /api proxy to backend"
  referenced_security_group_id = aws_security_group.backend.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_egress_rule" "frontend_https" {
  security_group_id = aws_security_group.frontend.id
  description       = "HTTPS to AWS APIs (ECR, logs) via endpoints/NAT"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# ---------------------------------------------------------------- backend + scheduler tasks
resource "aws_security_group" "backend" {
  name        = "${local.name}-backend"
  description = "Backend API and scheduler ECS tasks: inbound only from ALB and frontend tasks"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${local.name}-backend" }
}

resource "aws_vpc_security_group_ingress_rule" "backend_from_alb" {
  security_group_id            = aws_security_group.backend.id
  description                  = "API from ALB"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_ingress_rule" "backend_from_frontend" {
  security_group_id            = aws_security_group.backend.id
  description                  = "API from frontend nginx proxy"
  referenced_security_group_id = aws_security_group.frontend.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_egress_rule" "backend_to_db" {
  security_group_id            = aws_security_group.backend.id
  description                  = "PostgreSQL"
  referenced_security_group_id = aws_security_group.db.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_egress_rule" "backend_https" {
  security_group_id = aws_security_group.backend.id
  description       = "HTTPS to AWS APIs and the ITSM webhook"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# FedLine / sponsor-bank egress (VPN or PrivateLink) would be a dedicated egress rule here, scoped to the
# bank's CIDRs or the PrivateLink endpoint's security group, e.g. SFTP/22 or HTTPS/443.

# ---------------------------------------------------------------- RDS
resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "RDS PostgreSQL: inbound only from backend/scheduler tasks"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${local.name}-db" }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_backend" {
  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from ECS backend tasks"
  referenced_security_group_id = aws_security_group.backend.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

# ---------------------------------------------------------------- VPC interface endpoints
resource "aws_security_group" "vpc_endpoints" {
  name        = "${local.name}-vpce"
  description = "Interface VPC endpoints: HTTPS from within the VPC"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${local.name}-vpce" }
}

resource "aws_vpc_security_group_ingress_rule" "vpce_https" {
  security_group_id = aws_security_group.vpc_endpoints.id
  description       = "HTTPS from VPC"
  cidr_ipv4         = var.vpc_cidr
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
