"""Integration test for the gateway's tool-id proxy.

Stands up a fake upstream on 4001 that answers the load balancer's health
check and streams a tool call, puts the real proxy in front on 4000, and
checks the three things that would take the gateway down or leave the bug in
place: the health check still returns 200, a streamed tool call comes through
with a rewritten id, and two calls do not collide.
"""
import json
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

SSE = (
    b'event: content_block_start\n'
    b'data: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"tool_use","id":"call_0","name":"Bash","input":{}}}\n'
    b'\n'
    b'event: content_block_delta\n'
    b'data: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"input_json_delta","partial_json":"{\\"command\\":\\"ls\\"}"}}\n'
    b'\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b'{"status":"healthy"}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        self.rfile.read(n)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        for part in SSE.split(b"\n\n"):
            if not part:
                continue
            data = part + b"\n\n"
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")


def main() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", 4901), Upstream)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    import os
    env = dict(os.environ, TOOL_ID_PROXY_UPSTREAM="http://127.0.0.1:4901")
    proc = subprocess.Popen(
        [sys.executable, "ops/gateway/tool_id_proxy.py", "4900"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
    )
    try:
        for _ in range(60):
            try:
                if requests.get("http://127.0.0.1:4900/health/liveliness", timeout=2).status_code:
                    break
            except Exception:
                time.sleep(0.5)

        h = requests.get("http://127.0.0.1:4900/health/liveliness", timeout=5)
        assert h.status_code == 200, f"health check returned {h.status_code}"
        print("   raw health body:", repr(h.text))
        assert "healthy" in h.text, h.text
        print("PASS  health check 200 and body intact")

        ids = []
        for _ in range(2):
            r = requests.post("http://127.0.0.1:4900/v1/messages", json={"x": 1},
                              stream=True, timeout=10)
            text = b"".join(r.iter_content(None)).decode()
            found = re.findall(r'"id":\s*"(call_[A-Za-z0-9]+)"', text)
            assert found, f"no tool id in stream: {text[:200]}"
            ids.append(found[0])
            assert '"partial_json"' in text, "arguments delta lost"
            assert "message_stop" in text, "stream truncated"
        print(f"PASS  streamed tool ids rewritten: {ids}")
        assert all(i != "call_0" for i in ids), ids
        assert ids[0] != ids[1], "two responses reused an id"
        print("PASS  ids are unique across responses")

        # The four working harnesses do not go through /v1/messages, and their
        # responses must come back exactly as litellm produced them.
        r = requests.post("http://127.0.0.1:4900/v1/chat/completions",
                          json={"x": 1}, stream=True, timeout=10)
        other = b"".join(r.iter_content(None)).decode()
        assert '"call_0"' in other, (
            "a chat-completions response was rewritten; it must be relayed untouched"
        )
        print("PASS  /v1/chat/completions relayed untouched (call_0 preserved)")
        return 0
    finally:
        proc.terminate()
        srv.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
