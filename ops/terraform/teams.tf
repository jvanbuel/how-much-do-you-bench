# Team keys: the values, and where they are kept. Telling the gateway to accept
# them is `just ops::register-keys`, which is a curl against the admin route
# rather than a resource here.
#
# It was a resource, using BerriAI's own terraform provider, until that was
# tested against a live gateway: litellm_key ignores the `key` argument and
# mints its own value, and then does not record that value in state either --
# `key` reads null, and the only trace left is the hash LiteLLM stores. A key
# nobody can retrieve is not a key. The raw API does honour a supplied `key` and
# returns it, which is what the recipe uses.
#
# The gateway's admin route is public for that recipe's sake. See
# gateway_public.tf, and narrow admin_cidrs before the event.

locals {
  # A list rather than a map so the file reads as what it is: the teams.
  teams = toset(yamldecode(file("${path.module}/../teams.yaml")).teams)
}

resource "random_password" "team_key" {
  for_each = local.teams

  length  = 40
  special = false
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

  tags = { team = each.key }
}

# The names only. The keys are read with `just ops::team-keys`, never from an
# output that lands in a log.
output "teams" {
  value = sort(tolist(local.teams))
}
