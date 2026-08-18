# Team keys, minted by the gateway and owned by terraform.
#
# The list is ops/teams.yaml. Adding a name and applying mints that team a key;
# removing one deletes the key at the gateway and the copy in SSM, which is the
# whole of revocation -- the previous shape left a removed team still able to
# reach the model until the master key was rotated.
#
# This is why the gateway's admin routes are reachable at all: terraform runs
# where an operator runs, not inside the VPC. See gateway_public.tf, and narrow
# admin_cidrs before the event.

locals {
  # A list rather than a map so the file reads as what it is: the teams.
  teams = toset(yamldecode(file("${path.module}/../teams.yaml")).teams)
}

# Written by `just ops::master-key`, which has to run once before the first
# apply: the provider authenticates with it, and there is no key to mint keys
# with until it exists.
data "aws_ssm_parameter" "litellm_master_key" {
  name            = "/${local.name}/litellm-master-key"
  with_decryption = true
}

provider "litellm" {
  api_base = "https://${local.gateway_fqdn}"
  api_key  = data.aws_ssm_parameter.litellm_master_key.value
}

# Generated here rather than by the gateway, because `key` is a settable
# argument: a value known at plan time is one the SSM parameter can carry
# without terraform having to apply the gateway first, and the two cannot
# disagree about what a team's key is.
resource "random_password" "team_key" {
  for_each = local.teams

  length  = 40
  special = false
}

resource "litellm_key" "team" {
  for_each = local.teams

  key        = "sk-${random_password.team_key[each.key].result}"
  key_alias  = "team-${each.key}"
  models     = ["gemma"]
  rpm_limit  = var.rate_limit_rpm
  max_budget = var.team_budget

  # The route this provider talks to does not exist until that rule does, and
  # terraform has no other reason to order them.
  depends_on = [aws_lb_listener_rule.gateway_admin]
}

# Where a human, and the API, read a team's key. SecureString, so reading one is
# a decrypt: AWS credentials for this account are the whole access rule.
resource "aws_ssm_parameter" "team_key" {
  for_each = local.teams

  name = "/${local.name}/team-keys/${each.key}"
  type = "SecureString"

  # Write-only: the value goes to SSM and not into terraform state. It is the
  # same string the gateway was told to accept, so the parameter and the key
  # cannot drift. Bump the version to push a rotation -- terraform cannot read
  # the current value back to compare it.
  value_wo         = "sk-${random_password.team_key[each.key].result}"
  value_wo_version = 1

  # The key has to exist at the gateway before it is handed to anyone.
  depends_on = [litellm_key.team]

  tags = { team = each.key }
}

# The names only. The keys are read with `just ops::team-keys`, never from an
# output that lands in a log.
output "teams" {
  value = sort(tolist(local.teams))
}
