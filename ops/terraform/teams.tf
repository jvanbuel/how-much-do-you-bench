# Team keys: generated here, stored where only the AWS account can read them,
# and registered with the gateway separately.
#
# Terraform owns the secret material rather than the gateway, because the thing
# that needs to check a key at submit time is the API, and the thing that needs
# to hand one to a team is a human with AWS credentials. Neither can reach
# LiteLLM's admin API: it is internal on purpose, including for us. So the value
# is generated here and `just ops::register-keys` tells the gateway to accept
# exactly it -- /key/generate takes a caller-supplied key.

locals {
  # A list rather than a map so the file reads as what it is: the teams.
  teams = toset(yamldecode(file("${path.module}/../teams.yaml")).teams)
}

resource "random_password" "team_key" {
  for_each = local.teams

  length  = 40
  special = false
}

resource "aws_ssm_parameter" "team_key" {
  for_each = local.teams

  # One parameter per team under a shared path, so the API can read the whole
  # set in one call and a human can read exactly one.
  name  = "/${local.name}/team-keys/${each.key}"
  type  = "SecureString"
  value = "sk-${random_password.team_key[each.key].result}"

  tags = { team = each.key }
}

# The names only. The keys are readable with AWS credentials and the recipes
# below, never from an output that lands in a log.
output "teams" {
  value = sort(tolist(local.teams))
}
