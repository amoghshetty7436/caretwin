resource "aws_ecr_repository" "api" {
  name                 = "${var.name}-api"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_repository" "web" {
  name                 = "${var.name}-web"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_lifecycle_policy" "keep_recent" {
  for_each   = { api = aws_ecr_repository.api.name, web = aws_ecr_repository.web.name }
  repository = each.value
  policy = jsonencode({ rules = [{ rulePriority = 1, description = "keep last 10",
    selection = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 },
  action = { type = "expire" } }] })
}

resource "aws_cloudwatch_log_group" "main" {
  name              = "/ecs/${var.name}"
  retention_in_days = 14
}

resource "aws_ecs_cluster" "main" {
  name = "${var.name}-${var.environment}"
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

locals {
  log = { logDriver = "awslogs", options = {
  awslogs-group = aws_cloudwatch_log_group.main.name, awslogs-region = var.region, awslogs-stream-prefix = "ecs" } }
  secret_arn = aws_secretsmanager_secret.app.arn
}

# --- Neo4j ---------------------------------------------------------------------------
resource "aws_ecs_task_definition" "neo4j" {
  family                   = "${var.name}-neo4j"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 3072
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  volume {
    name = "neo4j-data"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.neo4j.id
      transit_encryption = "ENABLED"
    }
  }
  container_definitions = jsonencode([{
    name         = "neo4j", image = var.neo4j_image, essential = true
    portMappings = [{ containerPort = 7687, protocol = "tcp" }]
    environment = [{ name = "NEO4J_server_memory_heap_max__size", value = "1G" },
    { name = "NEO4J_server_memory_pagecache_size", value = "512M" }]
    secrets          = [{ name = "NEO4J_AUTH", valueFrom = "${local.secret_arn}:NEO4J_AUTH::" }]
    mountPoints      = [{ sourceVolume = "neo4j-data", containerPath = "/data" }]
    logConfiguration = local.log
  }])
}

resource "aws_ecs_service" "neo4j" {
  name            = "neo4j"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.neo4j.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  # single-writer database: stop the old task before starting a new one
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.data.id, aws_security_group.app.id]
    assign_public_ip = true
  }
  service_registries { registry_arn = aws_service_discovery_service.neo4j.arn }
}

# --- API -----------------------------------------------------------------------------
resource "aws_ecs_task_definition" "api" {
  family                   = "${var.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([{
    name         = "api", image = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}", essential = true
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    environment = [
      { name = "CARETWIN_NEO4J_URI", value = "bolt://neo4j.${var.name}.local:7687" },
      { name = "CARETWIN_LLM_PROVIDER", value = var.llm_provider },
      { name = "CARETWIN_LLM_BASE_URL", value = var.llm_base_url },
      { name = "CARETWIN_LLM_MODEL", value = var.llm_model },
      { name = "WEB_CONCURRENCY", value = "2" },
    ]
    secrets = [
      { name = "CARETWIN_DATABASE_URL", valueFrom = "${local.secret_arn}:CARETWIN_DATABASE_URL::" },
      { name = "CARETWIN_NEO4J_PASSWORD", valueFrom = "${local.secret_arn}:CARETWIN_NEO4J_PASSWORD::" },
      { name = "CARETWIN_LLM_API_KEY", valueFrom = "${local.secret_arn}:CARETWIN_LLM_API_KEY::" },
    ]
    logConfiguration = local.log
  }])
}

resource "aws_ecs_service" "api" {
  name                              = "api"
  cluster                           = aws_ecs_cluster.main.id
  task_definition                   = aws_ecs_task_definition.api.arn
  desired_count                     = var.api_desired_count
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 120
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }
  depends_on = [aws_lb_listener.http]
}

resource "aws_appautoscaling_target" "api" {
  max_capacity       = 6
  min_capacity       = var.api_desired_count
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${var.name}-api-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension
  service_namespace  = aws_appautoscaling_target.api.service_namespace
  target_tracking_scaling_policy_configuration {
    target_value = 60
    predefined_metric_specification { predefined_metric_type = "ECSServiceAverageCPUUtilization" }
  }
}

# --- Web (nginx + static React build) ---------------------------------------------
resource "aws_ecs_task_definition" "web" {
  family                   = "${var.name}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name             = "web", image = "${aws_ecr_repository.web.repository_url}:${var.web_image_tag}", essential = true
    portMappings     = [{ containerPort = 8080, protocol = "tcp" }]
    environment      = [{ name = "API_UPSTREAM", value = "http://${aws_lb.main.dns_name}" }]
    logConfiguration = local.log
  }])
}

resource "aws_ecs_service" "web" {
  name            = "web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 8080
  }
  depends_on = [aws_lb_listener.http]
}
