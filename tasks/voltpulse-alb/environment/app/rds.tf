# Mixed DB subnet group: public AZ1 + private AZ2.
# RDS is placed in AZ1 (the public subnet) because availability_zone = local.az,
# but publicly_accessible = false means its DNS resolves to a private IP only.
resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db"
  subnet_ids = [aws_subnet.public.id, aws_subnet.private_2.id]

  tags = {
    Name = "${var.project_name}-db-subnet-group"
  }
}

resource "aws_db_instance" "postgres" {
  identifier     = "${var.project_name}-postgres"
  engine         = "postgres"
  engine_version = "16"
  instance_class = "db.t4g.micro"

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  availability_zone      = local.az
  publicly_accessible    = false

  username                    = "postgres"
  manage_master_user_password = true

  skip_final_snapshot = true

  tags = {
    Name = "${var.project_name}-postgres"
  }
}
