# The gateway, reachable from outside the VPC.
#
# Participants have no AWS access in this account, so they cannot reach the
# private address the workers use and they cannot sign for Bedrock themselves.
# A public inference endpoint plus a per-team key is the one setup that works
# for a laptop, a remote environment and the graded workers alike, which is
# three ways of running a task collapsed into one.
#
# Two independent things keep the admin plane closed. LiteLLM already scopes
# virtual keys -- a team key gets 401 on /key/generate and /spend/logs, and
# only ever sees its own key -- and the listener below forwards nothing but
# /v1/*, so key minting, spend and model config are unreachable even if that
# scoping ever regressed. Budgets and rpm limits bound what a leaked key costs.

resource "aws_lb_target_group" "gateway" {
  name        = "${local.name}-gateway"
  port        = 4000
  protocol    = "HTTP"
  vpc_id      = data.aws_vpc.default.id
  target_type = "ip"

  health_check {
    path    = "/health/liveliness"
    matcher = "200"
  }
}

# An allow-list, not a deny-list: anything that is not inference gets refused,
# so a route added by a future LiteLLM release is closed until someone decides
# otherwise. Priorities sit below the viewer's, which occupies 10 and 20.
resource "aws_lb_listener_rule" "gateway_inference" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 30

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.gateway.arn
  }

  condition {
    host_header {
      values = [local.gateway_fqdn]
    }
  }

  condition {
    path_pattern {
      # Chat, models, embeddings and responses all live under /v1. The bare
      # /health check is deliberately not here: it would report the gateway's
      # upstream state to anyone who asked.
      values = ["/v1/*"]
    }
  }
}

# The admin API, reachable so terraform can manage keys directly rather than
# through an SSM tunnel. It is protected by the master key -- LiteLLM refuses
# these routes without it -- but that key is a single bearer token that can mint
# and delete every team's key, rewrite the model config and read all spend, and
# copies of it sit in SSM, in .env files and in terraform state. So it is also
# gated on source address: narrow admin_cidrs before the event and this stops
# being an internet-facing admin plane.
#
# Still an allow-list. These are the routes the litellm provider uses; a route a
# future release adds is refused by the rule below until someone decides
# otherwise.
resource "aws_lb_listener_rule" "gateway_admin" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 29

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.gateway.arn
  }

  condition {
    host_header {
      values = [local.gateway_fqdn]
    }
  }

  condition {
    path_pattern {
      # Only what terraform manages, which is keys. An ALB rule allows five
      # condition values in total across every condition, and the host header
      # and the source range are two of them -- so this cannot become a list of
      # every admin route anyway. Add a route here when something manages one.
      values = ["/key/*"]
    }
  }

  condition {
    source_ip {
      values = local.admin_cidrs
    }
  }
}

resource "aws_lb_listener_rule" "gateway_deny_rest" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 31

  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      status_code  = "404"
      message_body = "only /v1 is served here"
    }
  }

  condition {
    host_header {
      values = [local.gateway_fqdn]
    }
  }
}

output "gateway_url" {
  value = "https://${local.gateway_fqdn}/v1"
}
