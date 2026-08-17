# Backend EC2 security group — no ingress yet, so nothing can reach it.
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
