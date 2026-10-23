output "alb_dns_name" {
  description = "Public DNS name of the ALB; point your hostname (CNAME/alias) at it."
  value       = aws_lb.this.dns_name
}

output "alb_zone_id" {
  description = "Route 53 hosted zone ID of the ALB, for alias records."
  value       = aws_lb.this.zone_id
}

output "ecr_repository_urls" {
  description = "ECR repository URLs keyed by image (backend, frontend)."
  value       = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = aws_ecs_cluster.this.name
}

output "ecs_service_names" {
  description = "ECS service names."
  value = {
    backend   = aws_ecs_service.backend.name
    scheduler = aws_ecs_service.scheduler.name
    frontend  = aws_ecs_service.frontend.name
  }
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint (host:port)."
  value       = aws_db_instance.this.endpoint
}

output "rds_master_secret_arn" {
  description = "Secrets Manager ARN of the RDS-managed master credentials."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

output "database_url_secret_arn" {
  description = "Secrets Manager ARN for ACH_DATABASE_URL (operator must set the real password)."
  value       = aws_secretsmanager_secret.database_url.arn
}

output "vault_key_secret_arn" {
  description = "Secrets Manager ARN for ACH_VAULT_KEY (operator must set a real Fernet key)."
  value       = aws_secretsmanager_secret.vault_key.arn
}

output "nacha_archive_bucket" {
  description = "S3 bucket (Object Lock, COMPLIANCE mode) for archived Nacha files."
  value       = aws_s3_bucket.nacha_archive.bucket
}

output "kms_key_arn" {
  description = "Customer-managed KMS key ARN."
  value       = aws_kms_key.main.arn
}

output "alarm_topic_arn" {
  description = "SNS topic receiving CloudWatch alarms."
  value       = aws_sns_topic.alarms.arn
}

output "private_subnet_ids" {
  description = "Private subnet IDs (ECS tasks; attach sponsor-bank VPN/PrivateLink routes here)."
  value       = aws_subnet.private[*].id
}
