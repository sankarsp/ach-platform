variable "region" {
  description = "AWS region. The platform is designed for us-east-1 (closest to FedACH / most sponsor banks)."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project name, used as a prefix for resource names."
  type        = string
  default     = "ach-platform"
}

variable "environment" {
  description = "Deployment environment name (e.g. production, staging)."
  type        = string
  default     = "production"
}

variable "owner" {
  description = "Owning team, recorded in tags."
  type        = string
  default     = "payments-engineering"
}

variable "cost_center" {
  description = "Cost center, recorded in tags."
  type        = string
  default     = "payments"
}

# ---------------------------------------------------------------- network
variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.40.0.0/16"
}

variable "nat_gateway_per_az" {
  description = "One NAT gateway per AZ (HA) instead of a single shared NAT gateway (cheaper)."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------- edge
variable "acm_certificate_arn" {
  description = "ARN of an issued ACM certificate (us-east-1) for the public hostname served by the ALB."
  type        = string
}

variable "waf_rate_limit_per_5min" {
  description = "Max requests per source IP in any 5-minute window before WAF blocks."
  type        = number
  default     = 2000
}

variable "alb_deletion_protection" {
  description = "Enable deletion protection on the ALB."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------- application
variable "backend_image_tag" {
  description = "Immutable tag of the backend image in ECR (e.g. git SHA)."
  type        = string
}

variable "frontend_image_tag" {
  description = "Immutable tag of the frontend image in ECR (e.g. git SHA)."
  type        = string
}

variable "backend_desired_count" {
  description = "Desired number of API tasks (minimum 2 for AZ redundancy)."
  type        = number
  default     = 2

  validation {
    condition     = var.backend_desired_count >= 2
    error_message = "backend_desired_count must be at least 2."
  }
}

variable "backend_max_count" {
  description = "Upper bound for API task auto scaling."
  type        = number
  default     = 6
}

variable "frontend_desired_count" {
  description = "Desired number of frontend (nginx) tasks."
  type        = number
  default     = 2
}

variable "backend_cpu" {
  description = "Fargate CPU units for backend and scheduler tasks."
  type        = number
  default     = 512
}

variable "backend_memory" {
  description = "Fargate memory (MiB) for backend and scheduler tasks."
  type        = number
  default     = 1024
}

variable "scheduler_interval_seconds" {
  description = "ACH_SCHEDULER_INTERVAL_SECONDS for the scheduler service."
  type        = number
  default     = 60
}

variable "bank_name" {
  description = "ACH_BANK_NAME (ODFI name written into Nacha headers)."
  type        = string
}

variable "bank_routing" {
  description = "ACH_BANK_ROUTING (ODFI routing number)."
  type        = string
}

variable "fed_routing" {
  description = "ACH_FED_ROUTING (immediate destination, FedACH)."
  type        = string
  default     = "011000015"
}

variable "cors_origins" {
  description = "ACH_CORS_ORIGINS, comma separated (e.g. https://ach.example.com)."
  type        = string
}

variable "itsm_webhook_url" {
  description = "ACH_ITSM_WEBHOOK_URL used by the application for incident creation. Empty disables."
  type        = string
  default     = ""
}

variable "route_api_via_frontend" {
  description = "true: ALB sends /api/* to the frontend nginx, which strips /api and proxies to backend:8000 over ECS Service Connect (backend serves routes at /). false: ALB forwards /api/* straight to the backend target group (requires the backend to serve under /api)."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------- database
variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.m6g.large"
}

variable "db_engine_version" {
  description = "PostgreSQL engine version (major only lets RDS pick the latest minor)."
  type        = string
  default     = "16"
}

variable "db_allocated_storage" {
  description = "Initial storage (GiB)."
  type        = number
  default     = 100
}

variable "db_max_allocated_storage" {
  description = "Storage autoscaling ceiling (GiB)."
  type        = number
  default     = 500
}

variable "db_name" {
  description = "Initial database name."
  type        = string
  default     = "ach"
}

variable "db_username" {
  description = "Master username (password is generated and rotated by RDS in Secrets Manager)."
  type        = string
  default     = "ach_admin"
}

variable "db_backup_retention_days" {
  description = "Automated backup retention (days, max 35)."
  type        = number
  default     = 35
}

# ---------------------------------------------------------------- file archive
variable "nacha_archive_retention_days" {
  description = "Default S3 Object Lock (COMPLIANCE mode) retention for archived Nacha files. 2557 days ~= 7 years."
  type        = number
  default     = 2557
}

variable "nacha_archive_glacier_after_days" {
  description = "Transition archived files to Glacier Flexible Retrieval after this many days."
  type        = number
  default     = 90
}

# ---------------------------------------------------------------- logging / alerting
variable "log_retention_days" {
  description = "CloudWatch Logs retention for application, WAF and flow logs."
  type        = number
  default     = 400
}

variable "alarm_email" {
  description = "Optional email address subscribed to the alarm SNS topic. Empty = no subscription."
  type        = string
  default     = ""
}

variable "itsm_alarm_endpoint" {
  description = "Optional HTTPS endpoint (ServiceNow / PagerDuty / JSM) subscribed to the alarm SNS topic. Empty = none."
  type        = string
  default     = ""
}

variable "extra_tags" {
  description = "Additional tags merged into the common tag set."
  type        = map(string)
  default     = {}
}
