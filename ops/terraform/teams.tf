# Team keys: generated here, registered at the gateway, and stored where only
# this AWS account can read them.
#
# Adding a name to ops/teams.yaml and applying mints that team a key. Removing
# it deletes the key at the gateway and the parameter that held it, which is the
# whole of revocation -- verified by destroying one and watching the gateway
# return 401.
#
# The provider is ncecere/litellm rather than BerriAI's own. BerriAI's ignores
# the `key` argument and mints its own value, then records no value in state:
# `key` reads null and the only trace is the hash LiteLLM keeps. A key nobody
# can retrieve is not a key. Both were tested against a running gateway.
#
# This is why the gateway's admin routes are on the load balancer: terraform
# runs where an operator runs, not inside the VPC. See gateway_public.tf, and
# narrow admin_cidrs before the event.

locals {
  # A list rather than a map so the file reads as what it is: the teams.
  teams = toset(yamldecode(file("${path.module}/../teams.yaml")).teams)
}

# Generated here rather than by the gateway, so the value is known at plan time
# and the SSM parameter can carry it in the same apply.
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
  # the gateway has to be running the master key it is authenticated with.
  depends_on = [aws_lb_listener_rule.gateway_admin, aws_ecs_service.gateway]

  lifecycle {
    # A virtual key is derived from the master key, so rotating the master
    # invalidates every one of these. Recreating them is the honest response.
    replace_triggered_by = [random_password.litellm_master]
  }
}

provider "litellm" {
  api_base = "https://${local.gateway_fqdn}"
  # The value terraform generated, not a read-back of it: nothing has to exist
  # before the first apply.
  api_key = "sk-${random_password.litellm_master.result}"
}

# Where a human, and the API, read a team's key. SecureString, so reading one is
# a decrypt: AWS credentials for this account are the whole access rule.
resource "aws_ssm_parameter" "team_key" {
  for_each = local.teams

  name = "/${local.name}/team-keys/${each.key}"
  type = "SecureString"

  # Write-only: the value goes to SSM and not into terraform state. Bump the
  # version to push a rotation -- terraform cannot read the current value back
  # to compare it.
  value_wo         = "sk-${random_password.team_key[each.key].result}"
  value_wo_version = 1

  # A key is not worth handing out before the gateway accepts it.
  depends_on = [litellm_key.team]

  tags = { team = each.key }
}

# The names only. The keys are read with `just ops::team-keys`, never from an
# output that lands in a log.
output "teams" {
  value = sort(tolist(local.teams))
}
