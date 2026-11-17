"""Host-side relay that exposes only AssemblyAI's docs MCP endpoint to the agent containers.

The docs MCP server and the docs website share a host (www.assemblyai.com), and the website
is blocked inside every container (decision 30). This relay forwards requests whose path starts
with /docs/mcp to https://www.assemblyai.com and refuses everything else, so the skill+mcp
condition gets the MCP server and nothing more.

Started and stopped by runner/run_all.py. Streams responses (MCP may answer with SSE).
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

UPSTREAM = "https://www.assemblyai.com"
ALLOWED_PREFIX = "/docs/mcp"
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "host", "content-length",
       "proxy-authorization", "proxy-authenticate", "accept-encoding"}


class Relay(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # body ends when the connection closes; no length needed for streams
    counts: dict[str, int] = {"forwarded": 0, "refused": 0}

    def log_message(self, *args) -> None:  # keep the console quiet
        pass

    def _handle(self) -> None:
        if not self.path.startswith(ALLOWED_PREFIX):
            Relay.counts["refused"] += 1
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"only /docs/mcp is relayed\n")
            return
        Relay.counts["forwarded"] += 1
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        try:
            r = requests.request(self.command, UPSTREAM + self.path, headers=headers, data=body, stream=True,
                                 timeout=(10, 120), allow_redirects=False)
        except requests.RequestException as exc:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(f"relay error: {type(exc).__name__}\n".encode())
            return
        self.send_response(r.status_code)
        for k, v in r.headers.items():
            if k.lower() not in HOP and k.lower() != "content-encoding":
                self.send_header(k, v)
        self.end_headers()
        try:
            for chunk in r.raw.stream(4096, decode_content=True):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            r.close()

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = _handle

    def do_OPTIONS(self) -> None:
        self._handle()


def start(port: int = 8765) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("0.0.0.0", port), Relay)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    s = start()
    print("relay on :8765, forwarding /docs/mcp only. Ctrl-C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        s.shutdown()
