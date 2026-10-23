# ---------------------------------------------------------------- SNS alarm topic
resource "aws_sns_topic" "alarms" {
  name              = "${local.name}-alarms"
  kms_master_key_id = aws_kms_key.main.id
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ITSM (ServiceNow / PagerDuty / JSM) HTTPS endpoint. The endpoint must confirm the SNS subscription.
resource "aws_sns_topic_subscription" "itsm" {
  count                           = var.itsm_alarm_endpoint == "" ? 0 : 1
  topic_arn                       = aws_sns_topic.alarms.arn
  protocol                        = "https"
  endpoint                        = var.itsm_alarm_endpoint
  endpoint_auto_confirms          = true
  confirmation_timeout_in_minutes = 5
}

locals {
  alarm_actions = [aws_sns_topic.alarms.arn]
}

# ---------------------------------------------------------------- ALB
resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${local.name}-alb-target-5xx"
  alarm_description   = "Backend/frontend targets returned 5xx responses"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { LoadBalancer = aws_lb.this.arn_suffix }
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "alb_elb_5xx" {
  alarm_name          = "${local.name}-alb-elb-5xx"
  alarm_description   = "Load balancer itself returned 5xx (no healthy targets, timeouts)"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_ELB_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { LoadBalancer = aws_lb.this.arn_suffix }
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_hosts" {
  for_each = {
    backend  = aws_lb_target_group.backend.arn_suffix
    frontend = aws_lb_target_group.frontend.arn_suffix
  }

  alarm_name          = "${local.name}-${each.key}-unhealthy-hosts"
  alarm_description   = "Unhealthy ${each.key} targets behind the ALB"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    LoadBalancer = aws_lb.this.arn_suffix
    TargetGroup  = each.value
  }
  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}

# ---------------------------------------------------------------- RDS
resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${local.name}-rds-cpu-high"
  alarm_description   = "RDS CPU above 80% for 15 minutes"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.this.identifier }
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "rds_free_storage" {
  alarm_name          = "${local.name}-rds-free-storage-low"
  alarm_description   = "RDS free storage below 10 GiB"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 10 * 1024 * 1024 * 1024
  comparison_operator = "LessThanThreshold"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.this.identifier }
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# ---------------------------------------------------------------- ECS (Container Insights metrics)
resource "aws_cloudwatch_metric_alarm" "backend_running_tasks" {
  alarm_name          = "${local.name}-backend-running-below-desired"
  alarm_description   = "Backend API running task count is below desired count"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 3
  threshold           = 0
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  metric_query {
    id          = "gap"
    expression  = "running - desired"
    label       = "Running minus desired"
    return_data = true
  }

  metric_query {
    id = "running"
    metric {
      namespace   = "ECS/ContainerInsights"
      metric_name = "RunningTaskCount"
      period      = 60
      stat        = "Minimum"
      dimensions = {
        ClusterName = aws_ecs_cluster.this.name
        ServiceName = aws_ecs_service.backend.name
      }
    }
  }

  metric_query {
    id = "desired"
    metric {
      namespace   = "ECS/ContainerInsights"
      metric_name = "DesiredTaskCount"
      period      = 60
      stat        = "Maximum"
      dimensions = {
        ClusterName = aws_ecs_cluster.this.name
        ServiceName = aws_ecs_service.backend.name
      }
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "scheduler_not_running" {
  alarm_name          = "${local.name}-scheduler-not-running"
  alarm_description   = "The single scheduler task (release/settle/monitor) is not running"
  namespace           = "ECS/ContainerInsights"
  metric_name         = "RunningTaskCount"
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  dimensions = {
    ClusterName = aws_ecs_cluster.this.name
    ServiceName = aws_ecs_service.scheduler.name
  }
  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}

# ---------------------------------------------------------------- scheduler failures (log metric filter)
resource "aws_cloudwatch_log_metric_filter" "scheduler_tick_failed" {
  name           = "${local.name}-scheduler-tick-failed"
  log_group_name = aws_cloudwatch_log_group.ecs["scheduler"].name
  pattern        = "\"scheduler tick failed\""

  metric_transformation {
    name          = "SchedulerTickFailed"
    namespace     = "ACHPlatform/${var.environment}"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "scheduler_tick_failed" {
  alarm_name          = "${local.name}-scheduler-tick-failed"
  alarm_description   = "The ACH scheduler logged 'scheduler tick failed' (release/settle/monitor loop raised)"
  namespace           = "ACHPlatform/${var.environment}"
  metric_name         = "SchedulerTickFailed"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  depends_on = [aws_cloudwatch_log_metric_filter.scheduler_tick_failed]
}
