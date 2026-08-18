# ECS: services on Fargate, the worker on EC2.
#
# The split is forced by one fact: Harbor builds and runs the task container,
# which needs a Docker daemon. Fargate has none and cannot be given one, so the
# worker runs on an EC2 capacity provider with the host socket mounted. Nothing
# else needs Docker, so nothing else needs instances.
#
# The gateway is never exposed. Only the API sits behind a load balancer.

data "aws_caller_identity" "current" {}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_ssm_parameter" "ecs_ami" {
  # Architecture is one variable: the AMI, the Fargate runtime platform and the
  # docker build platform all derive from it, so they cannot drift apart.
  name = var.image_architecture == "arm64" ? "/aws/service/ecs/optimized-ami/amazon-linux-2023/arm64/recommended/image_id" : "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
}

data "aws_subnet" "default" {
  for_each = toset(data.aws_subnets.default.ids)
  id       = each.value
}

locals {
  name           = var.prefix
  dashboard_fqdn = "${var.dns_name}.${var.dns_zone}"
  traces_fqdn    = "${var.dns_name}-traces.${var.dns_zone}"
  gateway_fqdn   = "${var.dns_name}-llm.${var.dns_zone}"

  # Half the default VPC's subnets have no route to the internet gateway, so
  # placing tasks across all of them made reachability a coin flip: a Fargate
  # task landing on a private one cannot pull from ECR and dies with a
  # connection timeout. Only the public ones are usable without a NAT gateway.
  subnets = [for id, s in data.aws_subnet.default : id if s.map_public_ip_on_launch]

  # The default VPC has more subnets than availability zones, and two services
  # reject that outright: EFS permits one mount target per AZ, and an ALB
  # cannot attach to two subnets in the same AZ. One subnet per AZ satisfies
  # both; tasks still spread across every subnet.
  subnets_by_az = {
    for id, s in data.aws_subnet.default : s.availability_zone => id...
    if s.map_public_ip_on_launch
  }
  one_per_az = [for az, ids in local.subnets_by_az : sort(ids)[0]]
}

# ---------------------------------------------------------------- registries

resource "aws_ecr_repository" "api" {
  name         = "${local.name}-api"
  force_delete = true
}

resource "aws_ecr_repository" "worker" {
  name         = "${local.name}-worker"
  force_delete = true
}

resource "aws_ecr_repository" "gateway" {
  name         = "${local.name}-gateway"
  force_delete = true
}

# ------------------------------------------------------------------- secrets
#
# Terraform creates the parameter and then ignores its value, so the secret is
# never written from a plan and never appears in a diff. It does still land in
# state: a refresh reads the parameter back, and ignore_changes suppresses the
# diff, not the read. So the state bucket holds it, which is why that bucket
# blocks public access, is encrypted and is versioned. Anyone who can read it
# could read the parameter directly anyway.
#
# Generated here rather than set out of band: it was the one secret a human had
# to create before anything worked, and the terraform that mints team keys needs
# it too. Bedrock needs no secret at all -- the gateway signs as its task role.
#
# Rotating it is `terraform taint random_password.litellm_master` and an apply.
# Every virtual key is derived from it, so every team key is recreated with it;
# `replace_triggered_by` on those keys says so, and the fingerprint below rolls
# the services that hold the old value in memory.
resource "random_password" "litellm_master" {
  length  = 40
  special = false
}

resource "aws_ssm_parameter" "litellm_master_key" {
  name  = "/${local.name}/litellm-master-key"
  type  = "SecureString"
  value = "sk-${random_password.litellm_master.result}"
}

# --------------------------------------------------------------------- roles

data "aws_iam_policy_document" "assume" {
  for_each = toset(["ecs-tasks.amazonaws.com", "ec2.amazonaws.com"])

  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = [each.key]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.assume["ecs-tasks.amazonaws.com"].json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The execution role pulls the secrets at task start; the containers never hold
# credentials for them.
data "aws_iam_policy_document" "read_secrets" {
  statement {
    actions = ["ssm:GetParameters"]
    resources = [
      aws_ssm_parameter.litellm_master_key.arn,
    ]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.read_secrets.json
}

resource "aws_iam_role" "worker_task" {
  name               = "${local.name}-worker-task"
  assume_role_policy = data.aws_iam_policy_document.assume["ecs-tasks.amazonaws.com"].json
}

# The queue and table policy from core.tf: the worker is the only thing that
# writes results.
resource "aws_iam_role_policy_attachment" "worker_task" {
  role       = aws_iam_role.worker_task.name
  policy_arn = aws_iam_policy.worker.arn
}

resource "aws_iam_role" "api_task" {
  name               = "${local.name}-api-task"
  assume_role_policy = data.aws_iam_policy_document.assume["ecs-tasks.amazonaws.com"].json
}

resource "aws_iam_role_policy_attachment" "api_task" {
  role       = aws_iam_role.api_task.name
  policy_arn = aws_iam_policy.api.arn
}
# The gateway calls Bedrock with SigV4 as itself. This role is the whole
# credential story: nothing to mint, nothing to expire, nothing to refresh.
resource "aws_iam_role" "gateway_task" {
  name               = "${local.name}-gateway-task"
  assume_role_policy = data.aws_iam_policy_document.assume["ecs-tasks.amazonaws.com"].json
}

data "aws_iam_policy_document" "gateway_task" {
  statement {
    # bedrock-mantle is its own service namespace, not part of bedrock: the
    # endpoint answers bedrock-mantle:CreateInference against a project, and a
    # bedrock:InvokeModel grant does nothing for it.
    actions   = ["bedrock-mantle:CreateInference"]
    resources = ["arn:aws:bedrock-mantle:${var.region}:${data.aws_caller_identity.current.account_id}:project/default"]
  }
}

resource "aws_iam_role_policy" "gateway_task" {
  role   = aws_iam_role.gateway_task.id
  policy = data.aws_iam_policy_document.gateway_task.json
}

resource "aws_iam_role" "instance" {
  name               = "${local.name}-instance"
  assume_role_policy = data.aws_iam_policy_document.assume["ec2.amazonaws.com"].json
}

resource "aws_iam_role_policy_attachment" "instance_ecs" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

# Session Manager instead of SSH: no key pairs, no port 22.
resource "aws_iam_role_policy_attachment" "instance_ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "instance" {
  name = "${local.name}-instance"
  role = aws_iam_role.instance.name
}

# ---------------------------------------------------------------- networking

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public entry point for the dashboard"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "Dashboard, redirected to HTTPS"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.dashboard_cidrs
  }

  ingress {
    description = "Dashboard"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = var.dashboard_cidrs
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "service" {
  name        = "${local.name}-service"
  description = "Tasks and instances; internal traffic only"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "API from the load balancer"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "Trajectory viewer from the load balancer"
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "Gateway inference from the load balancer"
    from_port       = 4000
    to_port         = 4000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description = "Gateway and Postgres reachable only within this group"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_service_discovery_private_dns_namespace" "internal" {
  name = "${local.name}.internal"
  vpc  = data.aws_vpc.default.id
}

resource "aws_service_discovery_service" "gateway" {
  name = "gateway"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.internal.id
    dns_records {
      ttl  = 10
      type = "A"
    }
  }

  lifecycle {
    # The API always reports failure_threshold = 1 whatever is sent, so an
    # explicit health check block makes every apply propose a replacement,
    # which then fails because the gateway is still registered.
    ignore_changes = [health_check_custom_config]
  }
}

# --------------------------------------------------------------------- ALB

resource "aws_lb" "dashboard" {
  name               = "${local.name}-dashboard"
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = local.one_per_az
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name}-api"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = data.aws_vpc.default.id

  # /health, not /results: /results scans DynamoDB, so health checking it meant
  # a full table scan every thirty seconds per target and a throttled scan would
  # have taken the dashboard out for being unhealthy.
  health_check {
    path    = "/health"
    matcher = "200"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.dashboard.arn
  port              = 80
  protocol          = "HTTP"

  # Nothing is served over plain HTTP; it exists to send people to the
  # certificate.
  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# ------------------------------------------------------------ shared job logs

# One place to run `harbor view` over every graded rollout. Without it each
# worker replica can only show the runs it personally executed.
resource "aws_efs_file_system" "jobs" {
  creation_token = "${local.name}-jobs"
  encrypted      = true
}

resource "aws_efs_mount_target" "jobs" {
  for_each        = toset(local.one_per_az)
  file_system_id  = aws_efs_file_system.jobs.id
  subnet_id       = each.value
  security_groups = [aws_security_group.service.id]
}

resource "aws_efs_access_point" "jobs" {
  file_system_id = aws_efs_file_system.jobs.id

  posix_user {
    uid = 0
    gid = 0
  }

  root_directory {
    path = "/jobs"
    creation_info {
      owner_uid   = 0
      owner_gid   = 0
      permissions = "0755"
    }
  }
}

# --------------------------------------------------------------- ecs cluster

resource "aws_ecs_cluster" "this" {
  name = local.name
}

resource "aws_launch_template" "worker" {
  name_prefix   = "${local.name}-worker-"
  image_id      = data.aws_ssm_parameter.ecs_ami.value
  instance_type = var.worker_instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.instance.arn
  }

  vpc_security_group_ids = [aws_security_group.service.id]

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size = 200
      volume_type = "gp3"
    }
  }

  # EFS is mounted by the host, not by the task, and the worker bind-mounts that
  # host path. It has to be this way round: the worker drives the host's Docker
  # daemon, so every path Harbor asks Docker to bind is resolved on the host. An
  # ECS-managed EFS volume exists only inside the container, so Docker silently
  # created an empty /app/jobs on the host and every reward.txt the task
  # container wrote landed there, invisible to the worker. Rollouts then scored
  # zero with "no reward file found". Same path on both sides is the fix.
  user_data = base64encode(<<-EOT
    #!/bin/bash
    echo "ECS_CLUSTER=${local.name}" >> /etc/ecs/ecs.config
    echo "ECS_ENABLE_TASK_IAM_ROLE=true" >> /etc/ecs/ecs.config
    dnf install -y amazon-efs-utils
    mkdir -p /app/jobs
    echo "${aws_efs_file_system.jobs.id} /app/jobs efs _netdev,tls,accesspoint=${aws_efs_access_point.jobs.id} 0 0" >> /etc/fstab
    mount -a -t efs
  EOT
  )
}

resource "aws_autoscaling_group" "worker" {
  name                = "${local.name}-worker"
  vpc_zone_identifier = local.subnets
  min_size            = 0
  max_size            = var.worker_instances
  desired_capacity    = var.worker_instances

  launch_template {
    id      = aws_launch_template.worker.id
    version = "$Latest"
  }

  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }
}

resource "aws_ecs_capacity_provider" "worker" {
  name = "${local.name}-worker"

  auto_scaling_group_provider {
    auto_scaling_group_arn = aws_autoscaling_group.worker.arn

    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = 100
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 1
    }
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE", aws_ecs_capacity_provider.worker.name]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

# ------------------------------------------------------------------ logging

resource "aws_cloudwatch_log_group" "this" {
  for_each          = toset(["api", "gateway", "worker", "viewer"])
  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = 7
}

# ------------------------------------------------------------------ database
#
# RDS rather than a Postgres container on the cluster. The container version
# pinned the data to one instance's local disk, so replacing that instance took
# every virtual key with it, and it made the ASG that exists to be scaled to
# zero into something that had to stay up. A managed db.t4g.micro is about two
# cents an hour and removes both problems.

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "litellm" {
  name       = "${local.name}-litellm"
  subnet_ids = local.subnets
}

resource "aws_db_instance" "litellm" {
  identifier     = "${local.name}-litellm"
  engine         = "postgres"
  engine_version = "17"
  instance_class = var.db_instance_class

  allocated_storage = 20
  storage_encrypted = true

  db_name  = "litellm"
  username = var.db_username
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.litellm.name
  vpc_security_group_ids = [aws_security_group.service.id]

  # A four hour event: the keys are reminted per rollout and the results live
  # in DynamoDB, so there is nothing here worth a snapshot.
  skip_final_snapshot = true
  apply_immediately   = true
}
