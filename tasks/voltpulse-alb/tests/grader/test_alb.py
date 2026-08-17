"""Hidden grader for exercise 23 (VoltPulse: ALB in front of the private backend)."""
import re

import hcl_lite
import pytest


def _blocks(body: str, keyword: str) -> list[str]:
    """Return the contents of every flat `keyword { ... }` sub-block in body.

    Security-group ingress/egress blocks never nest further braces, so a
    non-greedy match to the next `}` is enough (no need for real brace
    matching here).
    """
    return re.findall(rf'{keyword}\s*\{{([^{{}}]*)\}}', body, re.DOTALL)


def _sg_label_refs(body: str) -> list[str]:
    """Every `aws_security_group.<label>` reference found in body."""
    return re.findall(r'aws_security_group\.(\w+)', body)


@pytest.fixture(scope="session")
def lbs(tf_text):
    return hcl_lite.resources(tf_text, "aws_lb")


@pytest.fixture(scope="session")
def target_groups(tf_text):
    return hcl_lite.resources(tf_text, "aws_lb_target_group")


@pytest.fixture(scope="session")
def security_groups(tf_text):
    return hcl_lite.resources(tf_text, "aws_security_group")


def test_alb_is_internet_facing_in_both_public_subnets(lbs):
    assert lbs, "no aws_lb resource found"
    # load_balancer_type defaults to "application" and internal defaults to
    # false when omitted, so only explicit wrong values disqualify.
    candidates = [
        lb for lb in lbs
        if not hcl_lite.attr_equals(lb.body, "load_balancer_type", "network")
        and not hcl_lite.attr_equals(lb.body, "load_balancer_type", "gateway")
        and not hcl_lite.attr_equals(lb.body, "internal", "true")
    ]
    assert candidates, (
        "no internet-facing application load balancer found (it must not be "
        "internal = true or of network/gateway type)"
    )
    assert any(
        re.search(r'aws_subnet\.public\b', lb.body)
        and re.search(r'aws_subnet\.public_2\b', lb.body)
        for lb in candidates
    ), "the ALB must sit in both aws_subnet.public and aws_subnet.public_2"


def test_alb_security_group_allows_http_from_internet(tf_text, lbs, security_groups):
    assert lbs, "no aws_lb resource found"
    lb = lbs[0]
    sg_labels = _sg_label_refs(lb.body)
    assert sg_labels, "the ALB has no security_groups attribute referencing a security group"

    def inline_ingress_allows_http(label: str) -> bool:
        sg = next((s for s in security_groups if s.name == label), None)
        if sg is None:
            return False
        return any(
            "80" in block and "cidr_blocks" in block and "0.0.0.0/0" in block
            for block in _blocks(sg.body, "ingress")
        )

    def standalone_rule_allows_http(label: str) -> bool:
        return any(
            hcl_lite.attr_equals(rule.body, "type", "ingress")
            and f"aws_security_group.{label}.id" in rule.body
            and "80" in rule.body
            and "cidr_blocks" in rule.body
            and "0.0.0.0/0" in rule.body
            for rule in hcl_lite.resources(tf_text, "aws_security_group_rule")
        )

    assert any(
        inline_ingress_allows_http(label) or standalone_rule_allows_http(label)
        for label in sg_labels
    ), "the ALB's security group must allow inbound HTTP (port 80) from 0.0.0.0/0"


def test_target_group_on_8080_http(target_groups):
    assert target_groups, "no aws_lb_target_group resource found"
    matches = [
        tg for tg in target_groups
        if hcl_lite.attr_equals(tg.body, "port", "8080")
        and hcl_lite.attr_equals(tg.body, "protocol", "HTTP")
    ]
    assert matches, "no aws_lb_target_group with port 8080 and protocol HTTP found"
    # target_type defaults to "instance" when omitted.
    assert any(
        hcl_lite.attr_equals(tg.body, "target_type", "instance")
        or "target_type" not in tg.body
        for tg in matches
    ), "the target group must use instance targets (target_type \"instance\" or omitted)"
    assert any(
        any(re.search(r'path\s*=\s*"/"', hc) for hc in _blocks(tg.body, "health_check"))
        for tg in matches
    ), "the target group needs a health_check with path = \"/\""


def test_target_group_attachment_registers_backend(tf_text):
    attachments = hcl_lite.resources(tf_text, "aws_lb_target_group_attachment")
    assert attachments, "no aws_lb_target_group_attachment resource found"
    assert any("aws_instance.backend" in a.body for a in attachments), (
        "no aws_lb_target_group_attachment references aws_instance.backend"
    )
    assert any(hcl_lite.attr_equals(a.body, "port", "8080") for a in attachments), (
        "the target group attachment must use port 8080"
    )


def test_listener_forwards_port_80(tf_text, target_groups):
    listeners = hcl_lite.resources(tf_text, "aws_lb_listener")
    assert listeners, "no aws_lb_listener resource found"
    tg_names = {tg.name for tg in target_groups if tg.name}
    # protocol defaults to HTTP on an ALB listener when omitted.
    matches = [
        l for l in listeners
        if hcl_lite.attr_equals(l.body, "port", "80")
        and not hcl_lite.attr_equals(l.body, "protocol", "HTTPS")
    ]
    assert matches, "no aws_lb_listener on port 80 (HTTP) found"
    assert any(
        any(f"aws_lb_target_group.{name}" in l.body for name in tg_names)
        for l in matches
    ), "the listener's default_action must forward to the target group"


def test_backend_gets_ingress_from_alb_sg_only(tf_text, security_groups):
    backend_sg = next((s for s in security_groups if s.name == "backend"), None)
    assert backend_sg is not None, "aws_security_group.backend is missing"

    inline_ingress = _blocks(backend_sg.body, "ingress")
    standalone_rules = [
        r for r in hcl_lite.resources(tf_text, "aws_security_group_rule")
        if "aws_security_group.backend.id" in r.body
        and hcl_lite.attr_equals(r.body, "type", "ingress")
    ]

    all_ingress_texts = inline_ingress + [r.body for r in standalone_rules]
    assert all_ingress_texts, (
        "the backend security group needs a new ingress rule allowing 8080 from the ALB"
    )

    for text in all_ingress_texts:
        assert "cidr_blocks" not in text, (
            "the backend security group must not have any CIDR-based ingress rule; "
            "the 8080 rule must reference the ALB's security group instead"
        )

    has_8080_from_sg = any(
        "8080" in text and ("source_security_group_id" in text or "security_groups" in text)
        for text in all_ingress_texts
    )
    assert has_8080_from_sg, (
        "the backend security group needs an ingress rule on port 8080 whose source is "
        "a security group reference (source_security_group_id or security_groups), not a CIDR block"
    )


def test_backend_instance_still_private(tf_text):
    instances = hcl_lite.resources(tf_text, "aws_instance")
    backend = next((i for i in instances if i.name == "backend"), None)
    assert backend is not None, "aws_instance.backend is missing"
    assert "aws_subnet.private.id" in backend.body, (
        "aws_instance.backend must stay in aws_subnet.private"
    )
    assert not re.search(r'associate_public_ip_address\s*=\s*true', backend.body), (
        "aws_instance.backend must not get a public IP"
    )
