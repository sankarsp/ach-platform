# Three services share one Fargate cluster in the private subnets:
#   backend   - API, >= 2 tasks across AZs behind the ALB, ACH_SCHEDULER_ENABLED=false, autoscaled on CPU
#   scheduler - same image, exactly ONE task, ACH_SCHEDULER_ENABLED=true; deployment settings guarantee the
#               old task stops before the new one starts so release/settle never runs twice concurrently
#   frontend  - nginx serving the SPA; proxies /api/ to backend:8000 via ECS Service Connect

resource "aws_ecs_cluster" "this" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  service_connect_defaults {
    namespace = aws_service_discovery_http_namespace.this.arn
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

resource "aws_service_discovery_http_namespace" "this" {
  name        = "${local.name}.internal"
  description = "ECS Service Connect namespace for ${local.name}"
}

# ---------------------------------------------------------------- log groups
resource "aws_cloudwatch_log_group" "ecs" {
  for_each = toset(["backend", "scheduler", "frontend"])

  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.main.arn
}

# ---------------------------------------------------------------- IAM: task execution role
data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:ecs:${var.region}:${local.account_id}:*"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    sid     = "ReadAppSecrets"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.database_url.arn,
      aws_secretsmanager_secret.vault_key.arn,
    ]
  }

  statement {
    sid       = "DecryptAppSecrets"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.main.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "read-app-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

# ---------------------------------------------------------------- IAM: backend task role (app permissions)
resource "aws_iam_role" "backend_task" {
  name               = "${local.name}-backend-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "backend_task" {
  statement {
    sid       = "ArchiveNachaFiles"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:GetObjectVersion", "s3:GetObjectRetention"]
    resources = ["${aws_s3_bucket.nacha_archive.arn}/*"]
  }

  statement {
    sid       = "ListArchive"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.nacha_archive.arn]
  }

  statement {
    sid       = "ArchiveEncryption"
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [aws_kms_key.main.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "RdsIamAuth"
    actions   = ["rds-db:connect"]
    resources = ["arn:${local.partition}:rds-db:${var.region}:${local.account_id}:dbuser:${aws_db_instance.this.resource_id}/ach_app"]
  }
}

resource "aws_iam_role_policy" "backend_task" {
  name   = "backend-app"
  role   = aws_iam_role.backend_task.id
  policy = data.aws_iam_policy_document.backend_task.json
}

# Frontend tasks need no AWS API access: they get a task role with no policies attached.
resource "aws_iam_role" "frontend_task" {
  name               = "${local.name}-frontend-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

# ---------------------------------------------------------------- task definitions
locals {
  backend_environment = [
    { name = "ACH_ENV", value = "production" },
    { name = "ACH_SEED_DEMO_DATA", value = "false" },
    { name = "ACH_BANK_NAME", value = var.bank_name },
    { name = "ACH_BANK_ROUTING", value = var.bank_routing },
    { name = "ACH_FED_ROUTING", value = var.fed_routing },
    { name = "ACH_CORS_ORIGINS", value = var.cors_origins },
    { name = "ACH_ITSM_WEBHOOK_URL", value = var.itsm_webhook_url },
    { name = "ACH_SCHEDULER_INTERVAL_SECONDS", value = tostring(var.scheduler_interval_seconds) },
    # Not read by the app yet (extra ACH_ vars are ignored); provided for the Nacha file archiver.
    { name = "ACH_NACHA_ARCHIVE_BUCKET", value = aws_s3_bucket.nacha_archive.bucket },
  ]

  backend_secrets = [
    { name = "ACH_DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
    { name = "ACH_VAULT_KEY", valueFrom = aws_secretsmanager_secret.vault_key.arn },
  ]

  backend_healthcheck = {
    command     = ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
    interval    = 30
    timeout     = 5
    retries     = 3
    startPeriod = 30
  }
}

resource "aws_ecs_task_definition" "backend" {
  family                   = "${local.name}-backend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.backend_cpu
  memory                   = var.backend_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.backend_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "backend"
      image     = local.backend_image
      essential = true
      user      = "10001:10001"
      portMappings = [
        { name = "http", containerPort = 8000, protocol = "tcp", appProtocol = "http" },
      ]
      environment = concat(local.backend_environment, [{ name = "ACH_SCHEDULER_ENABLED", value = "false" }])
      secrets     = local.backend_secrets
      healthCheck = local.backend_healthcheck
      linuxParameters = {
        initProcessEnabled = true
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs["backend"].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "api"
        }
      }
    },
  ])
}

resource "aws_ecs_task_definition" "scheduler" {
  family                   = "${local.name}-scheduler"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.backend_cpu
  memory                   = var.backend_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.backend_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "scheduler"
      image     = local.backend_image
      essential = true
      user      = "10001:10001"
      portMappings = [
        { containerPort = 8000, protocol = "tcp" },
      ]
      environment = concat(local.backend_environment, [{ name = "ACH_SCHEDULER_ENABLED", value = "true" }])
      secrets     = local.backend_secrets
      healthCheck = local.backend_healthcheck
      linuxParameters = {
        initProcessEnabled = true
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs["scheduler"].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "scheduler"
        }
      }
    },
  ])
}

resource "aws_ecs_task_definition" "frontend" {
  family                   = "${local.name}-frontend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.frontend_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "frontend"
      image     = local.frontend_image
      essential = true
      portMappings = [
        { name = "http", containerPort = 80, protocol = "tcp", appProtocol = "http" },
      ]
      healthCheck = {
        command     = ["CMD-SHELL", "wget -q -O /dev/null http://127.0.0.1/healthz || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 10
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.ecs["frontend"].name
          awslogs-region        = var.region
          awslogs-stream-prefix = "nginx"
        }
      }
    },
  ])
}

# ---------------------------------------------------------------- services
resource "aws_ecs_service" "backend" {
  name                              = "backend"
  cluster                           = aws_ecs_cluster.this.id
  task_definition                   = aws_ecs_task_definition.backend.arn
  desired_count                     = var.backend_desired_count
  launch_type                       = "FARGATE"
  platform_version                  = "LATEST"
  health_check_grace_period_seconds = 60
  enable_execute_command            = false
  propagate_tags                    = "SERVICE"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.backend.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.backend.arn
    container_name   = "backend"
    container_port   = 8000
  }

  # Exposes the API to the frontend nginx as http://backend:8000 (matches nginx.conf and docker compose).
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn

    service {
      port_name      = "http"
      discovery_name = "backend"
      client_alias {
        dns_name = "backend"
        port     = 8000
      }
    }
  }

  lifecycle {
    ignore_changes = [desired_count] # owned by application auto scaling
  }

  depends_on = [aws_lb_listener.https]
}

resource "aws_ecs_service" "scheduler" {
  name             = "scheduler"
  cluster          = aws_ecs_cluster.this.id
  task_definition  = aws_ecs_task_definition.scheduler.arn
  desired_count    = 1 # never scale: release/settle must run exactly once per tick
  launch_type      = "FARGATE"
  platform_version = "LATEST"
  propagate_tags   = "SERVICE"

  # Stop the old task before starting the new one so two schedulers never overlap.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.backend.id]
    assign_public_ip = false
  }
}

resource "aws_ecs_service" "frontend" {
  name                              = "frontend"
  cluster                           = aws_ecs_cluster.this.id
  task_definition                   = aws_ecs_task_definition.frontend.arn
  desired_count                     = var.frontend_desired_count
  launch_type                       = "FARGATE"
  platform_version                  = "LATEST"
  health_check_grace_period_seconds = 30
  propagate_tags                    = "SERVICE"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.frontend.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.frontend.arn
    container_name   = "frontend"
    container_port   = 80
  }

  # Client-only Service Connect: resolves backend:8000 to the backend service.
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.this.arn
  }

  depends_on = [aws_lb_listener.https, aws_ecs_service.backend]
}

# ---------------------------------------------------------------- backend auto scaling
resource "aws_appautoscaling_target" "backend" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.backend.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.backend_desired_count
  max_capacity       = var.backend_max_count
}

resource "aws_appautoscaling_policy" "backend_cpu" {
  name               = "${local.name}-backend-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.backend.service_namespace
  resource_id        = aws_appautoscaling_target.backend.resource_id
  scalable_dimension = aws_appautoscaling_target.backend.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}
