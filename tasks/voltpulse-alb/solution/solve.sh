#!/bin/bash
# The upstream reference answer, embedded file by file.
set -euo pipefail

mkdir -p /app
cat > /app/alb.tf <<'SOLUTION_EOF'
# Public-facing Application Load Balancer.
# End users have no way to reach the private backend, so the ALB sits in the
# public subnets and forwards traffic to the backend on port 8080.

resource "aws_security_group" "alb" {
  name        = "${var.project_name}-alb"
  description = "VoltPulse public ALB"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTP from the internet"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound (to the backend on 8080)"
  }

  tags = {
    Name = "${var.project_name}-alb-sg"
  }
}

resource "aws_lb" "main" {
  name               = "${var.project_name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = [aws_subnet.public.id, aws_subnet.public_2.id]

  tags = {
    Name = "${var.project_name}-alb"
  }
}

resource "aws_lb_target_group" "backend" {
  name        = "${var.project_name}-backend-tg"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  health_check {
    path                = "/"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = {
    Name = "${var.project_name}-backend-tg"
  }
}

resource "aws_lb_target_group_attachment" "backend" {
  target_group_arn = aws_lb_target_group.backend.arn
  target_id        = aws_instance.backend.id
  port             = 8080
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }
}
SOLUTION_EOF

mkdir -p /app
cat > /app/outputs.tf <<'SOLUTION_EOF'
output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "backend_instance_id" {
  description = "Backend EC2 instance ID — use with: aws ssm start-session --target <id> --region eu-west-1"
  value       = aws_instance.backend.id
}

output "backend_private_ip" {
  description = "Backend EC2 private IP"
  value       = aws_instance.backend.private_ip
}

output "rds_endpoint" {
  description = "RDS hostname (without port) — resolves to a private IP"
  value       = aws_db_instance.postgres.address
}

output "rds_secret_arn" {
  description = "Secrets Manager ARN for the DB master password"
  value       = aws_db_instance.postgres.master_user_secret[0].secret_arn
}

output "get_db_password" {
  description = "Command to retrieve the database password"
  value       = "aws secretsmanager get-secret-value --secret-id '${aws_db_instance.postgres.master_user_secret[0].secret_arn}' --query 'SecretString' --output text | jq -r '.password'"
}

output "alb_dns_name" {
  description = "Public ALB DNS name — hit this from a browser to reach the backend"
  value       = aws_lb.main.dns_name
}
SOLUTION_EOF

mkdir -p /app
cat > /app/security.tf <<'SOLUTION_EOF'
# Backend EC2 security group — reachable only via the ALB SG on 8080.
resource "aws_security_group" "backend" {
  name        = "${var.project_name}-backend"
  description = "VoltPulse backend EC2"
  vpc_id      = aws_vpc.main.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound"
  }

  tags = {
    Name = "${var.project_name}-backend-sg"
  }
}

# The backend is reachable only via the ALB SG (no public CIDR).
resource "aws_security_group_rule" "backend_http_from_alb" {
  type                     = "ingress"
  from_port                = 8080
  to_port                  = 8080
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.alb.id
  security_group_id        = aws_security_group.backend.id
  description              = "HTTP from public ALB only"
}

# RDS security group — ingress rules added as separate resources.
resource "aws_security_group" "rds" {
  name        = "${var.project_name}-rds"
  description = "VoltPulse RDS Postgres"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${var.project_name}-rds-sg"
  }
}

# Only the backend EC2 can reach the database.
resource "aws_security_group_rule" "rds_from_backend" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.backend.id
  security_group_id        = aws_security_group.rds.id
  description              = "Postgres from backend EC2"
}

# VPC endpoints SG — allows HTTPS from anywhere in the VPC.
resource "aws_security_group" "vpc_endpoints" {
  name        = "${var.project_name}-vpc-endpoints"
  description = "Security group for SSM VPC endpoints"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTPS from VPC"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  tags = {
    Name = "${var.project_name}-vpc-endpoints-sg"
  }
}
SOLUTION_EOF
