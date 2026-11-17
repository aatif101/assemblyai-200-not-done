"""Smoke test of the run -> classify -> report loop.

Points the runner at a loopback stub that accepts uploads and answers 404 to everything
else, with output redirected to a temp directory. The stub is not AssemblyAI; the only
claim tested is that the loop completes, writes one scrubbed record per request, and
that a failing control is reported as invalid rather than as a finding.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import twohundred.cli as cli
import twohundred.probes as probes
from conftest import FAKE_KEY


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        ok = self.path == "/v2/upload"
        payload = json.dumps({"upload_url": "http://stub.invalid/upload/1"} if ok else {"error": "stub"}).encode()
        self.send_response(200 if ok else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def stubbed(tmp_path, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    for name in ("async", "sync", "dictation", "tts"):
        monkeypatch.setitem(cli.ENDPOINTS, name, base)
    for name in ("streaming", "streaming_v2"):
        monkeypatch.setitem(cli.ENDPOINTS, name, "ws://127.0.0.1:9/ws")   # nothing listens here
    monkeypatch.setattr(probes, "A", base)
    monkeypatch.setattr(cli, "PHASE1", tmp_path / "phase1")
    monkeypatch.setattr(cli, "RAW", tmp_path / "phase1" / "raw")
    yield tmp_path / "phase1"
    srv.shutdown()


def test_full_loop_completes_and_reports_failed_controls_as_invalid(stubbed, capsys):
    assert cli.main(["probes", "run", "--repeats", "1"]) == 0
    assert cli.main(["probes", "classify"]) == 0
    assert cli.main(["probes", "classify", "--kill"]) == 0
    out = capsys.readouterr().out
    assert FAKE_KEY not in out

    matrix = json.loads((stubbed / "matrix.json").read_text())
    rows = {r["id"]: r for r in matrix["rows"]}
    assert len(rows) == len(probes.PROBES)
    assert rows["async.word_boost"]["class"] == "invalid_control"      # control got 404
    assert rows["stream.param.model_name"]["class"] == "invalid_control"  # control could not connect
    assert rows["async.lemur"]["class"] == "opaque_error"               # no control; 404 that names nothing
    assert not any(r["class"] in ("silent_accept", "honored", "accepted_with_warning") for r in rows.values())

    kill = json.loads((stubbed / "killtest.json").read_text())["summary"]["kill_test"]
    assert kill["verdict"].startswith("INCOMPLETE")

    raw = list((stubbed / "raw").glob("*.json"))
    assert len(raw) >= 2 * len(probes.PROBES) - 1
    assert all(FAKE_KEY not in p.read_text() for p in raw)
    assert (stubbed / "matrix.md").read_text().startswith("# Probe matrix")


def test_rerun_skips_what_is_already_recorded(stubbed, capsys):
    cli.main(["probes", "run", "--only", "tts.speed", "--repeats", "1"])
    capsys.readouterr()
    cli.main(["probes", "run", "--only", "tts.speed", "--repeats", "1"])
    assert capsys.readouterr().out.count("already recorded") == 2


def test_fixture_validation_fails_closed(stubbed, capsys):
    assert cli.main(["fixtures", "validate"]) == 1
    report = json.loads((stubbed / "fixture_validation.json").read_text())
    assert report["passed"] is False and len(report["checks"]) == 9
