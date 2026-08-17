terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Partial config: the bucket name carries the account id, so it lives in
  # backend.hcl, which is not committed. Initialise with
  #   terraform init -backend-config=backend.hcl
  # Native S3 locking, so no DynamoDB lock table to maintain.
  backend "s3" {
    key          = "hackathon/core.tfstate"
    region       = "eu-central-1"
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type = string
  # Frankfurt rather than the usual eu-west-1: Gemma 4 on bedrock-mantle is
  # served only from us-east-1, us-east-2, us-west-2 and eu-central-1, and the
  # model supports neither Geo nor Global cross-region inference, so no
  # inference profile can reach it from Ireland. Everything lives beside the
  # model rather than splitting the stack across two regions.
  default = "eu-central-1"
}

output "bedrock_mantle_base_url" {
  value = "https://bedrock-mantle.${var.region}.api.aws/openai/v1"
}

variable "prefix" {
  type    = string
  default = "de-benchmark"
}

# A rollout is one (submission, task) pair. Splitting the suite this way makes
# a failed task a retry of one task rather than a lost submission.
resource "aws_sqs_queue" "rollouts" {
  name = "${var.prefix}-rollouts"

  # Must exceed the worst case rollout, not the typical one: the agent alone is
  # allowed 900s by task.toml, and the image build and verifier run outside that
  # budget. At 900s a slow rollout was redelivered while the first copy was
  # still running, so two workers did the same work and the retry budget went
  # with it.
  visibility_timeout_seconds = 2400
  message_retention_seconds  = 14400

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.rollouts_dlq.arn
    # Attempts are spent by infrastructure, not just by bad rollouts: every
    # worker restart -- a deploy, an instance refresh, a scale-in -- kills
    # whatever was in flight and costs one. At 2, two restarts during an event
    # silently discarded a team's task. 5 survives a deploy and still bounds
    # a genuine poison message.
    maxReceiveCount = 5
  })
}

# Where a rollout goes when it has failed every attempt, so a poison message
# cannot loop for the length of the event.
resource "aws_sqs_queue" "rollouts_dlq" {
  name                      = "${var.prefix}-rollouts-dlq"
  message_retention_seconds = 86400
}

resource "aws_dynamodb_table" "results" {
  name         = "${var.prefix}-results"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "submission_id"
  range_key    = "task_id"

  attribute {
    name = "submission_id"
    type = "S"
  }

  attribute {
    name = "task_id"
    type = "S"
  }
}

data "aws_iam_policy_document" "worker" {
  statement {
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.rollouts.arn]
  }

  statement {
    actions   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.results.arn]
  }
}

resource "aws_iam_policy" "worker" {
  name   = "${var.prefix}-worker"
  policy = data.aws_iam_policy_document.worker.json
}

# The API is a producer, the worker is a consumer, and neither should hold the
# other's rights. Sharing one policy gave the API ReceiveMessage, letting it
# consume rollouts and starve the workers, while withholding the SendMessage
# and Scan it actually needs.
data "aws_iam_policy_document" "api" {
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.rollouts.arn]
  }

  statement {
    # Scan backs the results endpoint; PutItem writes the submission's _meta
    # row. Results themselves are the worker's to write.
    actions   = ["dynamodb:Scan", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.results.arn]
  }
}

resource "aws_iam_policy" "api" {
  name   = "${var.prefix}-api"
  policy = data.aws_iam_policy_document.api.json
}

output "queue_url" {
  value = aws_sqs_queue.rollouts.url
}

output "results_table" {
  value = aws_dynamodb_table.results.name
}

output "worker_policy_arn" {
  value = aws_iam_policy.worker.arn
}

variable "worker_instance_type" {
  type = string
  # Graviton: the build host is arm64, so nothing is emulated during the build
  # and the instances are cheaper for the same vCPU.
  default = "c7g.2xlarge"
}

variable "worker_instances" {
  type    = number
  default = 1
}

variable "worker_replicas" {
  type = number
  # 1000 rollouts x 142s over a four hour event needs 9.9 in flight; 12 covers
  # the spike when everyone submits at the end.
  default = 12
}

variable "rate_limit_rpm" {
  type    = number
  default = 60
}

# Derived from the tasks directory rather than restated. Every hand-maintained
# copy of this list drifted at least once: event.tfvars had six tasks while the
# suite had twenty-one, and off.tfvars had none at all, which silently reverted
# the deployed API to grading a single task.
#
# Leave null to grade every task. dev.tfvars sets it to a subset on purpose.
variable "task_ids" {
  type    = string
  default = null
}

locals {
  all_task_ids = sort([
    for f in fileset("${path.module}/../../tasks", "*/task.toml") : dirname(f)
  ])
  task_ids = coalesce(var.task_ids, join(",", local.all_task_ids))
}

variable "max_submissions" {
  type    = number
  default = 5
}

variable "dashboard_cidrs" {
  type = list(string)
  # Narrow this to the office or venue range before the event.
  default = ["0.0.0.0/0"]
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_username" {
  type    = string
  default = "litellm"
}

variable "dns_zone" {
  type    = string
  default = "playground.dataminded.cloud"
}

variable "dns_name" {
  type    = string
  default = "bench"
}

variable "image_architecture" {
  type    = string
  default = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.image_architecture)
    error_message = "image_architecture must be arm64 or x86_64."
  }
}

# Read by `just deploy-images`, so the build platform always matches the
# instances the images will run on.
output "image_architecture" {
  value = var.image_architecture
}
