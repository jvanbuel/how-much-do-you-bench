"""Tiny dependency-free HCL2 reader for grading Terraform exercises.

The benchmark must grade Terraform answers without requiring the `terraform`
binary or the `python-hcl2` package to be installed in the eval environment.
This module does just enough: it concatenates the workspace's ``*.tf`` files and
brace-matches top-level blocks so a grader can pull out a specific
``resource "<type>" "<name>" { ... }`` body and assert on tokens inside it.

It is intentionally not a full parser. It understands strings, `#` / `//` line
comments and `/* */` block comments well enough to skip braces inside them,
which is all the exercises need. Balanced-brace extraction also doubles as a
basic syntax check: a malformed file raises and the test fails.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Block:
    kind: str            # resource / data / provider / module / variable / ...
    labels: list[str]    # e.g. ["scaleway_k8s_pool", "standard"]
    body: str            # text between the outermost braces (excludes the braces)

    @property
    def type(self) -> str:
        return self.labels[0] if self.labels else ""

    @property
    def name(self) -> str:
        return self.labels[1] if len(self.labels) > 1 else ""


# A block head must start at the beginning of a line (HCL blocks do), and its
# keyword/labels/opening-brace all sit on that one line. Anchoring to the line
# start and keeping labels on the same line stops a word at the tail of a
# comment (e.g. a "/helm" in a URL) from gluing onto the next line's block.
_BLOCK_HEAD = re.compile(
    r'(?m)^[ \t]*'                      # start of line, optional indent
    r'(?P<kind>[A-Za-z_][\w-]*)'        # block keyword (resource, module, ...)
    r'(?P<labels>(?:[ \t]+"[^"]*"|[ \t]+[A-Za-z_][\w-]*)*)'  # quoted/bare labels
    r'[ \t]*\{'                         # opening brace, same line
)


def load_tf(target_dir: str | Path) -> str:
    """Concatenate every ``*.tf`` file under *target_dir* into one string."""
    target = Path(target_dir)
    files = sorted(target.glob("*.tf"))
    return "\n".join(f.read_text() for f in files)


def _match_brace(text: str, open_idx: int) -> int:
    """Return the index of the `}` matching the `{` at *open_idx*.

    Skips braces that live inside double-quoted strings or comments.
    """
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        # comments
        if c == "#" or (c == "/" and i + 1 < n and text[i + 1] == "/"):
            nl = text.find("\n", i)
            i = n if nl == -1 else nl
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        # strings
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\":
                    i += 1
                i += 1
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced braces in HCL")


def parse_blocks(text: str) -> list[Block]:
    """Return all top-level blocks found in *text*."""
    blocks: list[Block] = []
    pos = 0
    n = len(text)
    while pos < n:
        m = _BLOCK_HEAD.search(text, pos)
        if not m:
            break
        open_idx = m.end() - 1            # index of the `{`
        try:
            close_idx = _match_brace(text, open_idx)
        except ValueError:
            # Not actually a block (e.g. an interpolation); skip past the brace.
            pos = open_idx + 1
            continue
        labels = re.findall(r'"([^"]*)"|([A-Za-z_][\w-]*)', m.group("labels"))
        flat = [a or b for a, b in labels]
        blocks.append(Block(kind=m.group("kind"),
                            labels=flat,
                            body=text[open_idx + 1:close_idx]))
        pos = close_idx + 1
    return blocks


def resources(text: str, rtype: str | None = None) -> list[Block]:
    out = [b for b in parse_blocks(text) if b.kind == "resource"]
    if rtype is not None:
        out = [b for b in out if b.type == rtype]
    return out


_HEREDOC_HEAD = re.compile(r'<<(?P<dash>-?)(?P<tag>[A-Za-z_]\w*)[ \t]*\r?\n')


def heredocs(body: str) -> list[str]:
    """Return the contents of every heredoc (``<<TAG`` / ``<<-TAG`` … ``TAG``).

    For the indented ``<<-`` form the common leading whitespace is stripped, so
    the returned text matches what Terraform actually hands to the provider
    (important when the heredoc carries indentation-sensitive YAML). This lets a
    grader parse an embedded ``values`` document instead of only grepping the
    raw HCL text, which would miss a syntactically broken payload.
    """
    out: list[str] = []
    for m in _HEREDOC_HEAD.finditer(body):
        tag = m.group("tag")
        term = re.compile(r'(?m)^[ \t]*' + re.escape(tag) + r'[ \t]*$')
        tm = term.search(body, m.end())
        if not tm:
            continue
        content = body[m.end():tm.start()]
        if m.group("dash"):
            lines = content.split("\n")
            indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
            if indents:
                cut = min(indents)
                content = "\n".join(l[cut:] for l in lines)
        out.append(content)
    return out


def nested_blocks(body: str, name: str) -> list[str]:
    """Return the body of every nested ``<name> { ... }`` block inside *body*.

    Lets a grader assert that an attribute sits inside a specific sub-block
    (e.g. ``push_default_route`` inside ``ipam_config``) instead of anywhere in
    the resource body.
    """
    out: list[str] = []
    pat = re.compile(rf'(?m)^[ \t]*{re.escape(name)}[ \t]*\{{')
    for m in pat.finditer(body):
        open_idx = m.end() - 1
        try:
            close_idx = _match_brace(body, open_idx)
        except ValueError:
            continue
        out.append(body[open_idx + 1:close_idx])
    return out


def attr_equals(body: str, attr: str, value: str, ignore_case: bool = False) -> bool:
    """True if ``<attr> = <value>`` appears in *body* (value compared as token).

    Set *ignore_case* for values the provider treats case-insensitively (e.g.
    Scaleway node types, where ``DEV1-L`` and ``dev1-l`` are equivalent).
    """
    flags = re.IGNORECASE if ignore_case else 0
    pat = re.compile(rf'\b{re.escape(attr)}\s*=\s*"?{re.escape(value)}"?\b', flags)
    return bool(pat.search(body))


def has_token(body: str, token: str) -> bool:
    return token in body
