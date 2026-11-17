"""Plumbing tests against a loopback server on 127.0.0.1.

The server here is a stand-in that reflects what it received. It is not AssemblyAI and
does not imitate its behavior; these tests only prove that our client sends what the
probe describes and that nothing secret reaches a saved record. No results are produced.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import FAKE_KEY
from twohundred.config import load_settings
from twohundred.transport import Part, Req, http_call, ws_session

SETTINGS = load_settings()


class Reflect(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep test output quiet
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        seen = {"authorization": self.headers.get("authorization"),
                "content_type": self.headers.get("content-type"),
                "x_aai_model": self.headers.get("x-aai-model"),
                "part_order": [], "body_head": body[:40].decode("latin1")}
        for chunk in body.split(b"Content-Disposition: form-data; ")[1:]:
            seen["part_order"].append(chunk.split(b'name="')[1].split(b'"')[0].decode())
        status = 418 if self.path.endswith("/teapot") else 200
        payload = json.dumps(seen).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Request-Id", "abcdef0123456789" * 2)
        self.send_header("X-TTS-Unknown-Params", "zz")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture(scope="module")
def http_url():
    srv = HTTPServer(("127.0.0.1", 0), Reflect)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_raw_and_bearer_auth_are_sent_but_never_recorded(http_url):
    for mode, sent in (("raw", FAKE_KEY), ("bearer", f"Bearer {FAKE_KEY}")):
        rec = http_call(Req("http", f"{http_url}/x", json={"a": 1}, auth=mode), SETTINGS)
        dumped = json.dumps(rec)
        assert FAKE_KEY not in dumped                       # even though the server reflected it back
        assert "[REDACTED:ASSEMBLYAI_API_KEY]" in rec["response"]["json"]["authorization"]
        assert rec["response"]["json"]["authorization"].startswith("Bearer ") == (mode == "bearer")
        assert "Authorization" not in rec["request"]["headers"]
        assert sent  # documents intent: the real header carried the key


def test_no_auth_mode_sends_no_header(http_url):
    rec = http_call(Req("http", f"{http_url}/x", json={}, auth="none"), SETTINGS)
    assert rec["response"]["json"]["authorization"] is None


def test_multipart_part_order_is_exactly_as_given(http_url):
    for order in (("config", "audio"), ("audio", "config")):
        parts = [Part("config", "application/json", json={"k": 1}) if n == "config"
                 else Part("audio", "audio/wav", audio="short_en.wav") for n in order]
        rec = http_call(Req("http", f"{http_url}/x", multipart=parts, headers={"X-AAI-Model": "m"}), SETTINGS)
        assert rec["response"]["json"]["part_order"] == list(order)
        assert rec["response"]["json"]["x_aai_model"] == "m"
        assert [p["name"] for p in rec["request"]["multipart_order"]] == list(order)


def test_only_useful_response_headers_are_kept(http_url):
    rec = http_call(Req("http", f"{http_url}/x", json={}), SETTINGS)
    assert "X-TTS-Unknown-Params" in rec["response"]["headers"]
    assert "X-Request-Id" not in rec["response"]["headers"]   # opaque ids are dropped


def test_error_status_is_recorded_not_raised(http_url):
    assert http_call(Req("http", f"{http_url}/teapot", json={}), SETTINGS)["response"]["status"] == 418


def test_unreachable_host_is_a_transport_error():
    rec = http_call(Req("http", "http://127.0.0.1:9/x", json={}), SETTINGS)
    assert "transport_error" in rec and "response" not in rec


# ---------------------------------------------------------------- websocket plumbing


@pytest.fixture(scope="module")
def ws_url():
    from websockets.sync.server import serve

    received = {}

    def handler(conn):
        path = conn.request.path
        received["auth"] = conn.request.headers.get("Authorization")
        if "refuse" in path:
            conn.close(3006, "not acceptable")
            return
        conn.send(json.dumps({"type": "Begin", "path": path, "auth": received["auth"]}))
        n = 0
        for msg in conn:
            if isinstance(msg, bytes):
                n += len(msg)
            elif json.loads(msg).get("type") == "Terminate":
                conn.send(json.dumps({"type": "Termination", "bytes": n}))
                break

    srv = serve(handler, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.socket.getsockname()[1]
    yield f"ws://127.0.0.1:{port}/v3/ws"
    srv.shutdown()


def test_ws_session_sends_query_audio_and_terminate(ws_url):
    rec = ws_session(Req("ws", ws_url, query={"sample_rate": "16000", "keyterms_prompt": '["a b"]'},
                         audio="short_en.wav", send_seconds=0.5, pace=0.0), SETTINGS)
    ws = rec["ws"]
    assert ws["handshake_status"] == 101 and ws["begin"]["type"] == "Begin"
    assert "keyterms_prompt=%5B%22a+b%22%5D" in ws["begin"]["path"]
    assert ws["audio_seconds_sent"] == 0.5
    term = [m for m in ws["messages"] if m.get("type") == "Termination"]
    assert term and term[0]["bytes"] == 16000          # 0.5 s of 16 kHz 16-bit mono
    assert FAKE_KEY not in json.dumps(rec)             # server reflected the header; it is scrubbed


def test_ws_server_close_code_is_captured(ws_url):
    rec = ws_session(Req("ws", ws_url + "/refuse", query={}), SETTINGS)
    ws = rec["ws"]
    assert ws["closed_by_server"] is True and ws["server_close_code"] == 3006
    assert ws["server_close_reason"] == "not acceptable" and "begin" not in ws


def test_ws_unreachable_is_a_transport_error():
    rec = ws_session(Req("ws", "ws://127.0.0.1:9/v3/ws", query={}), SETTINGS)
    assert "transport_error" in rec
