"""Send one request, return one scrubbed record. No interpretation happens here.

Records are plain dicts so the classifier can be re-run offline from results/.
The Authorization header is never recorded; only the *scheme* used is.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import requests

from .audio import chunks, read_wav
from .config import AUDIO, Settings

HTTP_TIMEOUT = 90
POLL_TIMEOUT_S = 240
POLL_EVERY_S = 2.0
MAX_TEXT = 4000
MAX_WS_MESSAGES = 60

# Response headers worth keeping. Everything else (request ids, cookies, trace ids)
# is dropped: it adds nothing to the classification and widens the leak surface.
KEEP_HEADER = re.compile(
    r"^(content-type|retry-after|www-authenticate|allow|warning|deprecation|sunset|"
    r"x-tts-.*|x-ratelimit-.*|.*warn.*|.*deprecat.*|.*unknown.*)$",
    re.I,
)


@dataclass
class Part:
    name: str
    content_type: str
    json: Any = None            # JSON-serialised if set
    audio: str | None = None    # fixture file name under fixtures/audio
    send_as: str | None = None  # override the content type actually sent
    raw: bytes | None = None


@dataclass
class Req:
    kind: str                    # "http" | "ws"
    url: str
    method: str = "POST"
    auth: str = "raw"            # raw | bearer | none
    headers: dict[str, str] = field(default_factory=dict)
    json: Any = None
    multipart: list[Part] | None = None
    raw_body: bytes | None = None
    raw_body_desc: str | None = None
    poll: bool = False           # async transcript: follow the id to a terminal status
    pre: "Req | None" = None     # run first; "{{pre.upload_url}}" is substituted in json
    # websocket only
    query: dict[str, str] | None = None
    audio: str | None = None     # fixture to stream
    send_seconds: float = 3.0
    pace: float = 1.0            # 1.0 = wall-clock; 0 = as fast as possible
    expect_binary: bool = False  # http: response body is audio


def _auth_headers(req: Req, key: str) -> dict[str, str]:
    if req.auth == "raw":
        return {"Authorization": key}
    if req.auth == "bearer":
        return {"Authorization": f"Bearer {key}"}
    if req.auth == "none":
        return {}
    raise ValueError(f"unknown auth mode {req.auth!r}")


ACCOUNT_KEYS = ("project_id", "token_id")  # F6: account identifiers, never saved
UPLOAD_URL = re.compile(r"(cdn\.assemblyai\.com/upload/)[A-Za-z0-9-]{8,}")


def _trim_transcript(obj: Any) -> Any:
    """Keep what the classifier needs; drop bulky per-word arrays and account identifiers."""
    if not isinstance(obj, dict):
        return obj
    out = {}
    for k, v in obj.items():
        if k in ACCOUNT_KEYS:
            continue
        if isinstance(v, str):
            v = UPLOAD_URL.sub(lambda m: m.group(1) + "[REDACTED:upload_id]", v)
        if k in ("words", "unredacted_words") and isinstance(v, list):
            out[f"{k}_count"] = len(v)
        elif k in ("utterances", "unredacted_utterances") and isinstance(v, list):
            out[k] = [
                {kk: u.get(kk) for kk in ("speaker", "text") if isinstance(u, dict)}
                for u in v
            ]
        else:
            out[k] = v
    return out


def _describe_response(resp: requests.Response, binary: bool) -> dict[str, Any]:
    headers = {k: v for k, v in resp.headers.items() if KEEP_HEADER.match(k)}
    out: dict[str, Any] = {"status": resp.status_code, "headers": headers}
    ctype = resp.headers.get("content-type", "")
    body = resp.content
    parsed = None
    if "json" in ctype or (body[:1] in (b"{", b"[") and not binary):
        try:
            parsed = resp.json()
        except ValueError:
            parsed = None
    if parsed is not None:
        out["json"] = _trim_transcript(parsed)
    elif ctype.startswith("audio/") or (binary and resp.ok):
        out["bytes_len"] = len(body)
        out["bytes_head_hex"] = body[:12].hex(" ")
        out["starts_with_riff"] = body[:4] == b"RIFF"
    else:
        out["text"] = body[:MAX_TEXT].decode("utf-8", "replace")
    return out


def _describe_request(req: Req) -> dict[str, Any]:
    d: dict[str, Any] = {
        "kind": req.kind,
        "method": "GET (websocket upgrade)" if req.kind == "ws" else req.method,
        "url": req.url,
        "auth_scheme": {"raw": "Authorization: <key>", "bearer": "Authorization: Bearer <key>",
                        "none": "(no Authorization header)"}[req.auth],
        "headers": dict(req.headers),
    }
    if req.poll:
        d["poll"] = True
    if req.json is not None:
        d["json"] = req.json
    if req.multipart is not None:
        d["multipart_order"] = [
            {"name": p.name, "content_type": p.send_as or p.content_type,
             **({"json": p.json} if p.json is not None else {}),
             **({"audio": p.audio} if p.audio else {})}
            for p in req.multipart
        ]
    if req.raw_body_desc:
        d["raw_body"] = req.raw_body_desc
    if req.query is not None:
        d["query"] = req.query
    if req.kind == "ws":
        d.update(audio=req.audio, send_seconds=req.send_seconds, pace=req.pace)
    return d


def _send_http(req: Req, settings: Settings) -> requests.Response:
    headers = {**req.headers, **_auth_headers(req, settings.api_key)}
    kwargs: dict[str, Any] = {"headers": headers, "timeout": HTTP_TIMEOUT}
    if req.multipart is not None:
        files = []
        for p in req.multipart:
            if p.json is not None:
                payload: bytes = json.dumps(p.json).encode()
            elif p.audio is not None:
                payload = (AUDIO / p.audio).read_bytes()
            else:
                payload = p.raw or b""
            filename = p.audio if p.audio else None
            files.append((p.name, (filename, payload, p.send_as or p.content_type)))
        kwargs["files"] = files  # a list keeps the part order exactly as given
    elif req.raw_body is not None:
        kwargs["data"] = req.raw_body
    elif req.json is not None:
        kwargs["json"] = req.json
    return requests.request(req.method, req.url, **kwargs)


def poll_transcript(transcript_id: str, settings: Settings, base: str) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_TIMEOUT_S
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        r = requests.get(f"{base}/v2/transcript/{transcript_id}",
                         headers={"Authorization": settings.api_key}, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return {"poll_http_status": r.status_code, "poll_text": r.text[:MAX_TEXT]}
        last = r.json()
        if last.get("status") in ("completed", "error"):
            return _trim_transcript(last)
        time.sleep(POLL_EVERY_S)
    return {"poll_timeout": True, **_trim_transcript(last)}


def _substitute(obj: Any, mapping: dict[str, str]) -> Any:
    if isinstance(obj, str):
        for k, v in mapping.items():
            obj = obj.replace(k, v)
        return obj
    if isinstance(obj, dict):
        return {k: _substitute(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, mapping) for v in obj]
    return obj


def http_call(req: Req, settings: Settings) -> dict[str, Any]:
    rec: dict[str, Any] = {}
    t0 = time.monotonic()
    try:
        if req.pre is not None:
            pre_resp = _send_http(req.pre, settings)
            rec["pre"] = {"request": _describe_request(req.pre),
                          "response": _describe_response(pre_resp, False)}
            upload_url = ""
            try:
                upload_url = pre_resp.json().get("upload_url", "")
            except ValueError:
                pass
            req.json = _substitute(req.json, {"{{pre.upload_url}}": upload_url})
        rec["request"] = _describe_request(req)
        resp = _send_http(req, settings)
        rec["response"] = _describe_response(resp, req.expect_binary)
        if req.poll and resp.status_code == 200:
            tid = (rec["response"].get("json") or {}).get("id")
            if tid:
                base = req.url.split("/v2/")[0]
                rec["final"] = poll_transcript(tid, settings, base)
    except requests.RequestException as exc:
        rec.setdefault("request", _describe_request(req))
        rec["transport_error"] = f"{type(exc).__name__}: {exc}"
    rec["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
    return settings.scrubber.scrub_obj(rec)


# ----------------------------------------------------------------------------- websocket


def ws_session(req: Req, settings: Settings) -> dict[str, Any]:
    """Open a streaming session, optionally send paced audio, terminate, record everything."""
    from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus
    from websockets.sync.client import connect

    url = req.url
    if req.query:
        url += "?" + urllib.parse.urlencode(req.query)
    rec: dict[str, Any] = {"request": _describe_request(req)}
    ws_rec: dict[str, Any] = {"messages": [], "handshake_status": None}
    rec["ws"] = ws_rec
    t0 = time.monotonic()

    def note(raw: Any) -> None:
        if len(ws_rec["messages"]) >= MAX_WS_MESSAGES:
            ws_rec["messages_truncated"] = True
            return
        if isinstance(raw, bytes):
            ws_rec["messages"].append({"_binary_len": len(raw)})
            return
        try:
            msg = json.loads(raw)
        except ValueError:
            ws_rec["messages"].append({"_text": str(raw)[:MAX_TEXT]})
            return
        if isinstance(msg, dict):
            msg.pop("words", None)
            if msg.get("type") == "Begin":
                ws_rec["begin"] = msg
        ws_rec["messages"].append(msg)

    def note_close(exc: Any) -> None:
        """The server closed the socket: keep the close frame it sent (if any)."""
        frame = getattr(exc, "rcvd", None)
        ws_rec["closed_by_server"] = True
        ws_rec["server_close_code"] = getattr(frame, "code", None)
        ws_rec["server_close_reason"] = getattr(frame, "reason", None)

    def drain(ws: Any, timeout: float) -> bool:
        """Read whatever is available. Returns False once the socket is closed."""
        end = time.monotonic() + timeout
        while True:
            remaining = end - time.monotonic()
            try:
                note(ws.recv(timeout=max(remaining, 0)))
            except TimeoutError:
                return True
            except ConnectionClosed as exc:
                note_close(exc)
                return False
            if remaining <= 0:
                return True

    def session(ws: Any) -> None:
        ws_rec["handshake_status"] = 101
        alive = True
        # Wait for Begin (or an error/close) before sending anything.
        end = time.monotonic() + 10
        while alive and "begin" not in ws_rec and time.monotonic() < end:
            alive = drain(ws, 0.25)
        if alive and req.audio:
            pcm, sr, _ = read_wav(AUDIO / req.audio)
            sent = 0.0
            for chunk in chunks(pcm, ms=100, sample_rate=sr):
                if sent >= req.send_seconds:
                    break
                try:
                    ws.send(chunk)
                except ConnectionClosed as exc:
                    note_close(exc)
                    alive = False
                    break
                sent += 0.1
                if req.pace > 0:
                    time.sleep(0.1 * req.pace)
                alive = drain(ws, 0)
                if not alive:
                    break
            ws_rec["audio_seconds_sent"] = round(sent, 1)
        if alive:
            try:
                ws.send(json.dumps({"type": "Terminate"}))
            except ConnectionClosed as exc:
                note_close(exc)
                alive = False
        end = time.monotonic() + 15
        while alive and time.monotonic() < end:
            alive = drain(ws, 0.5)
            if any(isinstance(m, dict) and m.get("type") == "Termination" for m in ws_rec["messages"]):
                # Server confirmed; give it a moment to close, then stop waiting.
                drain(ws, 1.0)
                break

    try:
        with connect(url, additional_headers=_auth_headers(req, settings.api_key),
                     open_timeout=20, close_timeout=5, max_size=2**22) as ws:
            session(ws)
        ws_rec.setdefault("closed_by_server", False)
        # Final socket state. When we close first this is just our own 1000;
        # server_close_code above is the one that carries meaning.
        ws_rec["final_close_code"] = getattr(ws, "close_code", None)
        ws_rec["final_close_reason"] = getattr(ws, "close_reason", None)
    except InvalidStatus as exc:  # handshake refused with an HTTP status
        resp = exc.response
        ws_rec["handshake_status"] = resp.status_code
        body = getattr(resp, "body", b"") or b""
        ws_rec["handshake_body"] = body[:MAX_TEXT].decode("utf-8", "replace")
    except InvalidHandshake as exc:
        ws_rec["handshake_error"] = f"{type(exc).__name__}: {exc}"
    except (OSError, TimeoutError) as exc:
        rec["transport_error"] = f"{type(exc).__name__}: {exc}"
    rec["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
    return settings.scrubber.scrub_obj(rec)


def execute(req: Req, settings: Settings) -> dict[str, Any]:
    return ws_session(req, settings) if req.kind == "ws" else http_call(req, settings)
