# ACH platform - AWS infrastructure (us-east-1)

Production deployment of the ACH platform on ECS Fargate, RDS PostgreSQL and an ALB with WAF.

## What gets created

| Area | Resources |
|---|---|
| Network (`network.tf`) | VPC across 2 AZs with public (ALB, NAT), private (ECS tasks) and isolated (RDS, no internet route) subnets; NAT gateway (one, or one per AZ); gateway endpoint for S3; interface endpoints for ECR (api/dkr), Secrets Manager and CloudWatch Logs; VPC flow logs; the default security group allows nothing |
| Security groups (`security.tf`) | ALB: 443 (and 80 for the redirect) from the internet. ECS tasks: inbound only from the ALB (and frontend to backend for the `/api` proxy). RDS: 5432 only from backend/scheduler tasks |
| Encryption (`kms.tf`) | One customer-managed KMS key with automatic yearly rotation, used by RDS, Performance Insights, S3, Secrets Manager, CloudWatch Logs, ECR and SNS |
| Database (`rds.tf`) | PostgreSQL 16, Multi-AZ, gp3 with storage autoscaling, KMS-encrypted, deletion protection, final snapshot, 35-day PITR backups, Performance Insights, Enhanced Monitoring, IAM auth enabled, parameter group with `rds.force_ssl=1` and pgaudit |
| Secrets (`secrets.tf`) | RDS-managed master credentials (`manage_master_user_password`), plus the `ACH_DATABASE_URL` and `ACH_VAULT_KEY` secrets, created with placeholders the operator must replace |
| File archive (`s3.tf`) | Nacha archive bucket: versioning, SSE-KMS, public access blocked, TLS-only policy, Object Lock in **COMPLIANCE** mode (default 2557 days, about 7 years), moved to Glacier after 90 days. A separate SSE-S3 bucket holds ALB access logs |
| Registry (`ecr.tf`) | `ach-platform/backend` and `ach-platform/frontend` repositories with immutable tags, scan on push, KMS encryption and lifecycle rules |
| Compute (`ecs.tf`) | Fargate cluster with Container Insights and Service Connect. **backend**: 2 or more API tasks with CPU autoscaling and the scheduler off. **scheduler**: exactly one task with `ACH_SCHEDULER_ENABLED=true`, deployed stop-then-start so release and settle never run twice. **frontend**: nginx. Secrets are injected from Secrets Manager; logs go to KMS-encrypted log groups kept for 400 days |
| Edge (`alb.tf`) | HTTPS listener (ACM certificate, TLS 1.2+ policy `ELBSecurityPolicy-TLS13-1-2-2021-06`), HTTP to HTTPS 301 redirect, routing rules for `/api/*` and the backend's own paths, frontend as the default. WAFv2 with the AWS Common and Known-Bad-Inputs rule sets plus a per-IP rate limit; WAF logs have the Authorization header redacted |
| Monitoring (`monitoring.tf`) | SNS topic (KMS) with an optional email and ITSM HTTPS subscription. Alarms: ALB target and ELB 5xx, unhealthy hosts per target group, RDS CPU, RDS free storage, backend running tasks below desired, scheduler not running, and a log metric filter on `"scheduler tick failed"` |

**`/api` routing.** The SPA calls `/api/...`. The FastAPI app serves its routes at `/`, and an ALB cannot rewrite paths, so by default (`route_api_via_frontend = true`) the ALB sends `/api/*` to nginx. nginx strips the prefix and proxies to `backend:8000` over ECS Service Connect, which is the same path as in docker compose. `/health`, `/docs` and `/openapi.json` go straight to the backend target group.

**FedLine / sponsor-bank connectivity** is not provisioned. Comments in `network.tf` and `security.tf` mark where a Site-to-Site VPN, Transit Gateway or PrivateLink endpoint attaches: routes go on the private route tables, and egress belongs only to the backend security group.

## Prerequisites

- Terraform >= 1.6 and AWS credentials for the target account
- An issued ACM certificate in us-east-1 for your hostname
- Docker, to build images
- Optional: S3 bucket and DynamoDB table for remote state (see the commented `backend "s3"` block in `versions.tf`)

## Deploy

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in the cert ARN, bank identity, CORS origin, alarm targets

# 1. Create ECR first so images can be pushed before the services start
terraform init
terraform apply -target=aws_ecr_repository.this

# 2. Build and push immutable, SHA-tagged images
TAG=$(git rev-parse --short HEAD)
REG=$(terraform output -json ecr_repository_urls | jq -r .backend | cut -d/ -f1)
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin "$REG"
docker build -t "$REG/ach-platform/backend:$TAG"  ../../backend  && docker push "$REG/ach-platform/backend:$TAG"
docker build -t "$REG/ach-platform/frontend:$TAG" --build-arg VITE_API_URL=/api ../../frontend && docker push "$REG/ach-platform/frontend:$TAG"
# set backend_image_tag / frontend_image_tag = "$TAG" in terraform.tfvars

# 3. Everything else
terraform plan -out tfplan
terraform apply tfplan

# 4. Set the real secrets (the services will crash-loop until you do)
aws secretsmanager put-secret-value --secret-id "$(terraform output -raw vault_key_secret_arn)" \
  --secret-string "$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
# Read the RDS-managed password, URL-encode it, and write the full URL:
aws secretsmanager get-secret-value --secret-id "$(terraform output -raw rds_master_secret_arn)" --query SecretString --output text
aws secretsmanager put-secret-value --secret-id "$(terraform output -raw database_url_secret_arn)" \
  --secret-string 'postgresql+psycopg://ach_admin:<url-encoded-password>@<rds-host>:5432/ach?sslmode=require'

# 5. Roll the services so they pick up the secrets
for s in backend scheduler frontend; do
  aws ecs update-service --cluster "$(terraform output -raw ecs_cluster_name)" --service $s --force-new-deployment
done
```

Then point a DNS alias at `alb_dns_name` / `alb_zone_id`. Confirm the SNS email or ITSM subscription.

Back up the vault key outside AWS under dual control. If it is lost, stored account numbers cannot be decrypted.

**Database migrations.** The app currently creates its tables at startup (`create_all`), which is fine for a first deploy. Before any schema change in production, introduce Alembic and run `alembic upgrade head` as a one-off ECS task (`aws ecs run-task` with the backend task definition and a command override) before rolling the services. Create a least-privilege `ach_app` database role (IAM auth is already allowed in the task role) instead of running as the master user.

## Cost drivers (approximate, us-east-1, on-demand)

1. **RDS Multi-AZ `db.m6g.large`**: the largest line item (about $250/month plus storage, backups and PI)
2. **NAT gateway(s)**: about $33/month each plus data processing. `nat_gateway_per_az = true` doubles this
3. **Interface VPC endpoints**: 4 endpoints x 2 AZs, about $58/month
4. **Fargate**: 2 backend + 1 scheduler (0.5 vCPU / 1 GB) + 2 frontend (0.25 vCPU / 0.5 GB), about $70/month
5. **ALB + WAF**: about $20 + $10/month base, plus LCU and request charges
6. CloudWatch Logs ingestion and storage (400-day retention), Container Insights metrics, KMS ($1/key/month plus requests), S3/Glacier for archives

## Controls mapping

| Control | Implementation |
|---|---|
| Encryption at rest | Customer-managed KMS key (rotated yearly) for RDS, PI, S3 archive, Secrets Manager, CloudWatch Logs, ECR, SNS. The app additionally encrypts account numbers with a Fernet vault key |
| Encryption in transit | TLS 1.2+ at the ALB, HTTP redirected to HTTPS, `rds.force_ssl=1`, S3 policies deny non-TLS requests |
| Network isolation | 3-tier VPC; RDS in isolated subnets with no internet route; tasks in private subnets with no public IPs; security groups chained ALB to tasks to DB; AWS API traffic through VPC endpoints; default SG denies all |
| Perimeter protection | WAFv2 with AWS managed Common and Known-Bad-Inputs rule sets plus per-IP rate limiting; ALB drops invalid headers |
| Identity and least privilege | Separate execution and task roles. Execution can read only the 2 app secrets. The backend task can only write/read the archive bucket and connect via RDS IAM auth. The frontend task role has no permissions |
| Secrets management | No secrets in Terraform variables or task definitions. RDS rotates the master password; the vault key is set out of band so it never enters Terraform state |
| Audit logging | VPC flow logs, ALB access logs, WAF logs (Authorization redacted), RDS PostgreSQL logs with pgaudit (DDL and role), and application logs, all kept 400 days. Enable CloudTrail organisation-wide (not managed here) |
| Backups and resilience | Multi-AZ RDS, 35-day PITR, final snapshot, deletion protection on RDS and ALB, tasks spread across 2 AZs, ECS deployment circuit breaker with rollback |
| File retention | Nacha archive under S3 Object Lock COMPLIANCE (WORM), about 7 years by default, versioned, moved to Glacier after 90 days |
| Monitoring and incident response | CloudWatch alarms on availability, capacity and scheduler failures, routed via SNS to email and/or the ITSM endpoint |
| Exactly-once batch processing | A dedicated single-task scheduler service with stop-before-start deployments; API tasks run with the scheduler disabled |
