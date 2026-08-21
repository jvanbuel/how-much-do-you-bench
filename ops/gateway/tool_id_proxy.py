"""Make tool-call ids unique on their way out of the gateway.

The endpoint ids a tool call by its position within one response, and this
model makes one call per turn, so every call in a conversation comes back as
`call_0`. Claude Code aborts a `tool_use` whose id it has already handled, so
its first call runs and every later one is discarded -- which is what
`[Tool use interrupted]` means in its transcript. Measured: 76 calls in one
rollout, all `call_0`; with unique ids the same task runs eight clean turns and
stops on `end_turn`.

This cannot live in hooks.py with the other fixes. LiteLLM runs no post-call
hook on `/v1/messages` (their issue #27518), which is the only route Claude
Code speaks, so the rewrite has to happen outside LiteLLM. It sits in the same
container rather than a sidecar: litellm moves to 4001, this takes 4000, and
nothing in terraform changes.

Everything that is not a tool-call id is forwarded byte for byte, including the
health check the load balancer depends on.
"""
from __future__ import annotations

import os
import re
import socket
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

UPSTREAM = os.environ.get("TOOL_ID_PROXY_UPSTREAM", "http://127.0.0.1:4001")
# Off means forward untouched, so the rewrite can be disabled without a rebuild.
ENABLED = os.environ.get("TOOL_ID_PROXY_ENABLED", "1") != "0"

# Only the Anthropic route is touched. The OpenAI-shaped harnesses -- opencode,
# pi, trae-agent, the custom baseline -- pair a tool result with the call that
# precedes it, so duplicate ids cost them nothing, and all four score the same
# with them. Rewriting their ids would be a change none of them asked for on
# the day it matters. Everything outside this path is relayed untouched.
REWRITE_PATHS = ("/v1/messages",)

# Only ids the endpoint generates. A client-supplied id is left alone: it is
# echoed conversation history, and rewriting it would break the threading this
# exists to protect.
_ID = re.compile(rb'"id"\s*:\s*"(call_\d+)"')
_HOP = {"content-length", "transfer-encoding", "connection", "keep-alive",
        "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade"}


def _rewrite(chunk: bytes) -> bytes:
    """Give each `call_<n>` a fresh id. Cheap enough to run per line."""
    if b"call_" not in chunk:
        return chunk
    return _ID.sub(lambda m: b'"id": "call_' + uuid.uuid4().hex[:16].encode() + b'"', chunk)


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "tool-id-proxy"

    def log_message(self, *args):
        pass  # the gateway's own logs are the record

    def _should_rewrite(self) -> bool:
        path = self.path.split("?", 1)[0]
        return ENABLED and any(path.startswith(p) for p in REWRITE_PATHS)

    def _relay(self, method: str) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}

        try:
            up = requests.request(
                method, UPSTREAM + self.path, data=body, headers=headers,
                stream=True, timeout=900, allow_redirects=False,
            )
        except Exception as exc:  # upstream down: say so rather than hang
            self.send_response(502)
            self.send_header("content-type", "text/plain")
            self.end_headers()
            self.wfile.write(f"gateway proxy: {exc}".encode()[:500])
            return

        rewriting = self._should_rewrite()

        self.send_response(up.status_code)
        for k, v in up.headers.items():
            if k.lower() not in _HOP:
                self.send_header(k, v)
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()

        try:
            if not rewriting:
                # Every other route is a relay: raw bytes, in the sizes they
                # arrive, with nothing parsed or reassembled. This is the path
                # the four working harnesses take.
                for raw in up.raw.stream(65536, decode_content=False):
                    self._chunk(raw)
            elif "event-stream" in (up.headers.get("content-type") or ""):
                # Server-sent events are line-oriented and a tool call's id
                # never spans two lines, so line-at-a-time keeps the stream
                # flowing without buffering what the client reads as it goes.
                for line in up.iter_lines(decode_unicode=False):
                    self._chunk(_rewrite(line) + b"\n")
            else:
                self._chunk(_rewrite(up.content))
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client hung up; nothing to salvage

    def _chunk(self, data: bytes) -> None:
        if not data:
            return
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
        self.wfile.flush()

    def do_GET(self): self._relay("GET")
    def do_POST(self): self._relay("POST")
    def do_PUT(self): self._relay("PUT")
    def do_PATCH(self): self._relay("PATCH")
    def do_DELETE(self): self._relay("DELETE")
    def do_HEAD(self): self._relay("HEAD")
    def do_OPTIONS(self): self._relay("OPTIONS")


def _wait_for_upstream(host: str, port: int, timeout: float = 180.0) -> bool:
    """Hold the port closed until litellm answers.

    Binding first and 502ing would let the load balancer mark the target
    healthy against a gateway that cannot serve anything.
    """
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(2)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(1)
    return False


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
    up_host = UPSTREAM.split("//", 1)[-1].split(":")[0]
    up_port = int(UPSTREAM.rsplit(":", 1)[-1])
    print(f"tool-id-proxy: waiting for litellm on {up_host}:{up_port}", flush=True)
    if not _wait_for_upstream(up_host, up_port):
        print("tool-id-proxy: litellm never came up", file=sys.stderr, flush=True)
        raise SystemExit(1)
    print(f"tool-id-proxy: :{port} -> {UPSTREAM} (rewrite={ENABLED})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Proxy).serve_forever()
