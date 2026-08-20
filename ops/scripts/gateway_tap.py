"""A pass-through in front of the gateway that can rewrite tool-call ids.

The endpoint numbers tool calls by position within a response, so a model that
makes one call per turn returns `call_0` on every turn of the conversation.
LiteLLM's post-call hooks do not run on /v1/messages, so the gateway cannot fix
it there -- this sits outside and does.

With TAP_REWRITE_IDS=1 every `tool_use` id on the way back becomes unique. The
point is to test one claim: that Claude Code aborts a tool call whose id it has
already seen, which is what `[Tool use interrupted]` in its transcript would
mean. If the loop stops when the ids differ, the claim holds.

SSE is line-oriented, so rewriting happens per complete line; a data: line is
never split across chunks by the upstream.
"""
import json
import os
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin

import requests

UPSTREAM = os.environ["TAP_UPSTREAM"].rstrip("/") + "/"
OUT = os.environ.get("TAP_LOG", "/tmp/tap.jsonl")
REWRITE = os.environ.get("TAP_REWRITE_IDS") == "1"
NEEDLE = "Analyze a Harbor task trajectory"

lock = threading.Lock()
counter = {"n": 0, "rewritten": 0}
_ID = re.compile(rb'"id"\s*:\s*"(call_[A-Za-z0-9_]*)"')


def _fresh() -> bytes:
    return f'"id": "call_{uuid.uuid4().hex[:16]}"'.encode()


class Tap(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        url = urljoin(UPSTREAM, self.path.lstrip("/"))
        try:
            payload = json.loads(body)
            msgs = payload.get("messages", [])
            blob = json.dumps(msgs)
            with lock:
                counter["n"] += 1
                rec = {
                    "turn": counter["n"],
                    "n_messages": len(msgs),
                    "file_content_present": NEEDLE in blob,
                    "interrupted": blob.count("Tool use interrupted"),
                    "no_content": blob.count("(no content)"),
                    "payload_bytes": len(body),
                }
                with open(OUT, "a") as f:
                    f.write(json.dumps(rec) + "\n")
                if counter["n"] in (3, 6):
                    Path(f"{OUT}.body{counter['n']}.json").write_text(
                        json.dumps(payload, indent=1)[:400000]
                    )
        except Exception as exc:
            with lock, open(OUT, "a") as f:
                f.write(json.dumps({"parse_error": str(exc)}) + "\n")

        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "content-length", "connection")}
        up = requests.post(url, data=body, headers=headers, stream=True, timeout=600)

        self.send_response(up.status_code)
        for k, v in up.headers.items():
            if k.lower() not in ("content-length", "transfer-encoding", "connection"):
                self.send_header(k, v)
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()

        for raw in up.iter_lines(decode_unicode=False):
            line = b"" if raw is None else raw
            if REWRITE and b"tool_use" in line and b'"id"' in line:
                new, n = _ID.subn(lambda m: _fresh(), line)
                if n:
                    line = new
                    with lock:
                        counter["rewritten"] += n
            out = line + b"\n"
            self.wfile.write(hex(len(out))[2:].encode() + b"\r\n" + out + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    print(f"tap on :{port} -> {UPSTREAM} | rewrite_ids={REWRITE}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Tap).serve_forever()
