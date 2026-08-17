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
