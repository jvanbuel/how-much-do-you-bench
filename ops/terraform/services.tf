# Task definitions and services.
#
# Postgres and the gateway are addressed by service discovery DNS rather than
# by IP, so a task replacement does not require anything else to be updated.

locals {
  gateway_host = "gateway.${aws_service_discovery_private_dns_namespace.internal.name}"
  gateway_url  = "http://${local.gateway_host}:4000/v1"
  admin_url    = "http://${local.gateway_host}:4000"
}

# ------------------------------------------------------------------- gateway

resource "aws_ecs_task_definition" "gateway" {
  family                   = "${local.name}-gateway"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]

  runtime_platform {
    cpu_architecture        = upper(var.image_architecture)
    operating_system_family = "LINUX"
  }

  cpu                = 1024
  memory             = 2048
  execution_role_arn = aws_iam_role.execution.arn
  # SigV4 against Bedrock, instead of a bearer token somebody has to remember
  # to refresh.
  task_role_arn = aws_iam_role.gateway_task.arn

  container_definitions = jsonencode([{
    name      = "gateway"
    image     = "${aws_ecr_repository.gateway.repository_url}:latest"
    essential = true
    command   = ["--config", "/app/config.yaml", "--port", "4000"]
    environment = [
      { name = "DATABASE_URL", value = "postgresql://${var.db_username}:${random_password.db.result}@${aws_db_instance.litellm.endpoint}/litellm" },
      { name = "AWS_REGION", value = var.region },
      { name = "AWS_DEFAULT_REGION", value = var.region },
      # Not read by anything. Its only job is to change when the master key
      # changes, so the task definition changes, so the service rolls and the
      # container re-reads SSM. A rotated parameter restarts nothing by itself,
      # and a container holding the old value fails every mint with a 401.
      { name = "MASTER_KEY_FINGERPRINT", value = substr(sha256(aws_ssm_parameter.litellm_master_key.value), 0, 12) },
    ]
    secrets = [
      { name = "LITELLM_MASTER_KEY", valueFrom = aws_ssm_parameter.litellm_master_key.arn },
    ]
    portMappings = [{ containerPort = 4000 }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this["gateway"].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "gateway"
      }
    }
  }])
}

resource "aws_ecs_service" "gateway" {
  name            = "gateway"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.gateway.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.subnets
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = true # egress to Bedrock without a NAT gateway
  }

  service_registries {
    registry_arn = aws_service_discovery_service.gateway.arn
  }

  # Registered with the load balancer as well as service discovery: the workers
  # reach it privately, participants reach /v1 through the listener.
  load_balancer {
    target_group_arn = aws_lb_target_group.gateway.arn
    container_name   = "gateway"
    container_port   = 4000
  }

  depends_on = [aws_db_instance.litellm, aws_lb_listener.https]
}

# ----------------------------------------------------------------------- api

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]

  runtime_platform {
    cpu_architecture        = upper(var.image_architecture)
    operating_system_family = "LINUX"
  }

  cpu                = 512
  memory             = 1024
  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.api_task.arn

  volume {
    name = "jobs"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.jobs.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.jobs.id
        iam             = "DISABLED"
      }
    }
  }

  container_definitions = jsonencode([{
    name      = "api"
    image     = "${aws_ecr_repository.api.repository_url}:latest"
    essential = true
    environment = [
      { name = "QUEUE_URL", value = aws_sqs_queue.rollouts.url },
      { name = "RESULTS_TABLE", value = aws_dynamodb_table.results.name },
      { name = "BENCHMARK_REGION", value = var.region },
      { name = "TASKS", value = local.task_ids },
      { name = "MAX_SUBMISSIONS", value = tostring(var.max_submissions) },
      # Where the team keys live. Passed whole rather than rebuilt from a prefix
      # in two places, which is how the API ended up reading a path nothing set.
      { name = "TEAM_KEY_PATH", value = "/${local.name}/team-keys" },
      { name = "VIEWER_URL", value = var.viewer_replicas > 0 ? "https://${local.traces_fqdn}" : "" },
    ]
    # Read-only: the API surfaces job directories, the worker writes them.
    mountPoints  = [{ sourceVolume = "jobs", containerPath = "/app/jobs", readOnly = true }]
    portMappings = [{ containerPort = 8000 }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this["api"].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "api"
      }
    }
  }])
}

resource "aws_ecs_service" "api" {
  name            = "api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.subnets
    security_groups  = [aws_security_group.service.id]
    assign_public_ip = true # image pull without a NAT gateway
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.http]
}

# -------------------------------------------------------------------- worker
#
# The only service that needs a Docker daemon, hence the only one on EC2. It
# drives the host socket and starts sibling containers rather than nesting.

resource "aws_ecs_task_definition" "worker" {
  family = "${local.name}-worker"
  # bridge, not awsvpc: an awsvpc task consumes an ENI, and small instances
  # allow very few (a c7i.large has 3, minus one for the instance). That would
  # cap replicas per host on exactly the instance sizes worth using. The worker
  # needs no inbound traffic, and it still resolves the gateway through the
  # VPC's private DNS zone.
  network_mode             = "bridge"
  requires_compatibilities = ["EC2"]

  # Reserves what a replica *causes* to run, not what its own process uses. The
  # rollout container is a sibling started over the Docker socket, so ECS never
  # sees it: with the honest-looking 256/512 here, twelve replicas fit one host
  # on paper, managed scaling took the fleet from four instances to one, and all
  # twelve landed together -- 24 vCPU and 48GiB of rollouts asked of 8 and 16.
  #
  # The worst task in the suite, not the common one, and read from task.toml
  # rather than restated: three tasks ask for 6144MiB while this said 4608, and
  # a replica that draws a 6GiB task on a host packed for 4GiB ones is an OOM
  # kill that reads as "no trial result produced" and retries five times.
  # +512MiB for this container's own process beside the rollout it starts.
  cpu                = local.task_cpus * 1024
  memory             = local.task_memory_mb + 512
  execution_role_arn = aws_iam_role.execution.arn
  task_role_arn      = aws_iam_role.worker_task.arn

  volume {
    name      = "docker-socket"
    host_path = "/var/run/docker.sock"
  }

  # A host bind, not an ECS-managed EFS volume: the instance mounts EFS at this
  # same path (see the launch template) so the path means the same thing to the
  # worker and to the host Docker daemon it drives. An EFS volume here would
  # exist only inside the container, and Harbor's bind mounts would resolve
  # against a different, empty host directory.
  volume {
    name      = "jobs"
    host_path = "/app/jobs"
  }

  container_definitions = jsonencode([{
    name      = "worker"
    image     = "${aws_ecr_repository.worker.repository_url}:latest"
    essential = true
    environment = [
      { name = "QUEUE_URL", value = aws_sqs_queue.rollouts.url },
      { name = "RESULTS_TABLE", value = aws_dynamodb_table.results.name },
      { name = "BENCHMARK_REGION", value = var.region },
      { name = "GATEWAY_URL", value = local.gateway_url },
      { name = "GATEWAY_ADMIN_URL", value = local.admin_url },
      { name = "RATE_LIMIT_RPM", value = tostring(var.rate_limit_rpm) },
      # Not read by anything. Its only job is to change when the master key
      # changes, so the task definition changes, so the service rolls and the
      # container re-reads SSM. A rotated parameter restarts nothing by itself,
      # and a container holding the old value fails every mint with a 401.
      { name = "MASTER_KEY_FINGERPRINT", value = substr(sha256(aws_ssm_parameter.litellm_master_key.value), 0, 12) },
    ]
    secrets = [
      { name = "LITELLM_MASTER_KEY", valueFrom = aws_ssm_parameter.litellm_master_key.arn },
    ]
    mountPoints = [
      { sourceVolume = "docker-socket", containerPath = "/var/run/docker.sock" },
      { sourceVolume = "jobs", containerPath = "/app/jobs" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this["worker"].name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_replicas

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.worker.name
    weight            = 1
  }

  # Stop before starting. The reservations deliberately fill the hosts, so there
  # is no room for a replacement task to start alongside the one it replaces:
  # with the defaults (100/200) ECS holds every new task PENDING forever and the
  # deployment never completes. A queue consumer can afford this -- SIGTERM
  # returns the in-flight rollout to the queue, so the cost of stopping first is
  # one requeued message rather than a lost one.
  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 100

  # AZ rebalancing refuses to coexist with maximumPercent <= 100, and it is the
  # less useful half of the pair here: the placement strategies below already
  # spread, and a queue consumer does not care if one AZ carries a replica more
  # after a host is replaced.
  availability_zone_rebalancing = "DISABLED"

  # Spread across instances so losing one host costs a share of the rollouts in
  # flight rather than all of them.
  ordered_placement_strategy {
    type  = "spread"
    field = "attribute:ecs.availability-zone"
  }

  ordered_placement_strategy {
    type  = "spread"
    field = "instanceId"
  }

  depends_on = [aws_ecs_service.gateway]
}

output "dashboard_url" {
  value = "http://${aws_lb.dashboard.dns_name}"
}
