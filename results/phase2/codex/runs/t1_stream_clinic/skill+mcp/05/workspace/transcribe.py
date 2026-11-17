"""Replay PCM WAV audio to AssemblyAI v3 without persisting partial transcripts."""

import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlencode
import wave

from websockets.asyncio.client import connect


ENDPOINT = "wss://streaming.assemblyai.com/v3/ws"
PARAMETERS = {
    "speech_model": "universal-3-6-pro",
    "sample_rate": "16000",
    "encoding": "pcm_s16le",
    "domain": "medical-v1",
    "speaker_labels": "true",
    "speaker_labels_revision_interval_ms": "300000",
    "redact_pii": "true",
    "redact_pii_sub": "entity_name",
    # Never request unredacted drafts: the raw audit log must be safe to store.
    "include_partial_turns": "false",
    # Omit redact_pii_policies to enable every supported PII category.
}
BEGIN_TIMEOUT = 20
DRAIN_TIMEOUT = 30
CHUNK_FRAMES = 800  # 50 ms, 16-bit mono at 16 kHz


class TranscriptionError(Exception):
    """An error whose message contains no transcript or credentials."""


def validate_audio(audio):
    if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
            audio.getcomptype()) != (1, 2, 16000, "NONE"):
        raise TranscriptionError("Audio must be uncompressed 16-bit, 16 kHz mono WAV.")
    if audio.getnframes() == 0:
        raise TranscriptionError("Audio is empty.")


def validate_begin(frame):
    config = frame.get("configuration", {})
    expected = {
        "model": PARAMETERS["speech_model"],
        "domain": "medical-v1",
        "speaker_labels": True,
        "redact_pii": True,
    }
    if any(config.get(key) != value for key, value in expected.items()):
        raise TranscriptionError("AssemblyAI did not confirm the required model and privacy features.")
    if config.get("include_partial_turns") is True:
        raise TranscriptionError("AssemblyAI enabled unredacted partial turns.")


class Transcript:
    def __init__(self):
        self.turns = {}
        self.revisions = {}

    def accept(self, frame):
        if frame.get("type") == "Turn":
            if frame.get("end_of_turn") is not True:
                raise TranscriptionError("Unexpected partial turn despite partials being disabled.")
            order = frame["turn_order"]
            self.turns[order] = {
                "speaker": frame.get("speaker_label") or "UNKNOWN",
                "text": frame["transcript"],
            }
            if order in self.revisions:
                self.turns[order]["speaker"] = self.revisions[order]
        elif frame.get("type") == "SpeakerRevision":
            for revision in frame["revisions"]:
                order = revision["turn_order"]
                label = revision.get("speaker_label") or "UNKNOWN"
                self.revisions[order] = label
                if order in self.turns:
                    self.turns[order]["speaker"] = label

    def result(self):
        return {"turns": [self.turns[order] for order in sorted(self.turns)]}


async def send_audio(ws, audio):
    """Pace each chunk by its duration; never burst to catch up after a delay."""
    loop = asyncio.get_running_loop()
    frames_sent = 0
    while True:
        data = audio.readframes(CHUNK_FRAMES)
        if not data:
            break
        if len(data) % 2:
            raise TranscriptionError("WAV contains an incomplete PCM sample.")
        frames_sent += len(data) // 2
        if len(data) != CHUNK_FRAMES * 2:
            # The protocol requires >=50 ms, including the final packet.
            data = data.ljust(CHUNK_FRAMES * 2, b"\0")
        deadline = loop.time() + 0.05
        await ws.send(data)
        await asyncio.sleep(max(0, deadline - loop.time()))
    if frames_sent != audio.getnframes():
        raise TranscriptionError("WAV audio is truncated.")
    # Terminate flushes the last turn and triggers speaker refinement.
    await ws.send(json.dumps({"type": "Terminate"}))


async def receive(ws, audit, transcript, begun, terminated):
    async for raw in ws:
        # Keep the exact application message bytes, including whitespace and
        # field order. Do not log a parsed/SDK-normalized representation.
        payload = raw.encode("utf-8") if isinstance(raw, str) else raw
        audit.write(payload + b"\n")
        audit.flush()
        frame = json.loads(payload)
        if not isinstance(frame, dict):
            raise TranscriptionError("AssemblyAI sent a non-object message.")
        kind = frame.get("type")
        if kind == "Begin":
            validate_begin(frame)
            begun.set()
        elif kind == "Error" or frame.get("error"):
            raise TranscriptionError("AssemblyAI returned an error; see the audit log.")
        elif kind == "Termination":
            terminated.set()
            # Termination is the protocol's final message. Close our side even
            # if the peer leaves the transport open after confirming shutdown.
            return
        else:
            transcript.accept(frame)
    if not terminated.is_set():
        raise TranscriptionError("AssemblyAI closed without a Termination message.")


async def race_receiver(receiver, operation, timeout):
    """Propagate receiver failures immediately, even while sending or waiting."""
    task = asyncio.create_task(operation)
    try:
        done, _ = await asyncio.wait(
            {receiver, task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if receiver in done:
            receiver.result()
            if not task.done():
                raise TranscriptionError("AssemblyAI ended the session before audio completed.")
        if task not in done and not task.done():
            raise TranscriptionError("Timed out waiting for AssemblyAI.")
        await task
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def stream(audio, audit, api_key, endpoint=ENDPOINT):
    transcript = Transcript()
    begun = asyncio.Event()
    terminated = asyncio.Event()
    async with connect(
        endpoint + "?" + urlencode(PARAMETERS),
        additional_headers={"Authorization": api_key},
        open_timeout=BEGIN_TIMEOUT,
        close_timeout=5,
        max_size=8 * 1024 * 1024,
    ) as ws:
        receiver = asyncio.create_task(receive(ws, audit, transcript, begun, terminated))
        try:
            await race_receiver(receiver, begun.wait(), BEGIN_TIMEOUT)
            await race_receiver(receiver, send_audio(ws, audio),
                                audio.getnframes() / 16000 + 60)
            # Wait for all final turns and label revisions before returning.
            await asyncio.wait_for(asyncio.shield(receiver), DRAIN_TIMEOUT)
            if not terminated.is_set():
                raise TranscriptionError("Missing session termination confirmation.")
        finally:
            if not receiver.done():
                receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await receiver
    return transcript.result()


def write_result(path, result):
    # Publish only a complete successful result; use private permissions.
    fd, temporary = tempfile.mkstemp(prefix=".transcript-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    api_key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not api_key:
        raise TranscriptionError("ASSEMBLYAI_API_KEY is not set.")
    audit_path = Path(str(args.output) + ".frames.jsonl")
    # Never clobber source audio, including through symlinks/hard links.
    for target in (args.output, audit_path):
        if target.resolve() == args.audio.resolve() or (
                target.exists() and os.path.samefile(target, args.audio)):
            raise TranscriptionError("Output paths must differ from the input audio.")
    with wave.open(str(args.audio), "rb") as audio:
        validate_audio(audio)
        # A failed rerun must not leave an old success result at this path.
        args.output.unlink(missing_ok=True)
        with audit_path.open("wb") as audit:
            result = asyncio.run(stream(audio, audit, api_key))
        write_result(args.output, result)
    print(f"Transcribed {len(result['turns'])} turns.", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Transcription interrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        # Network exception strings may contain sensitive response data.
        message = str(exc) if isinstance(exc, TranscriptionError) else type(exc).__name__
        print(f"Transcription failed: {message}", file=sys.stderr)
        sys.exit(1)
