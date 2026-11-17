"""Stream a WAV file to AssemblyAI at real-time speed and collect a diarized,
PII-redacted, medical-mode transcript."""
import asyncio
import json
import os
import sys
import wave
from urllib.parse import urlencode

import websockets

CHUNK_MS = 50
PARAMS = {
    "speech_model": "universal-3-6-pro",
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "domain": "medical-v1",
    "speaker_labels": "true",
    "redact_pii": "true",
    "redact_pii_sub": "entity_name",
    "include_partial_turns": "false",  # never receive unredacted partials
}
URL = "wss://streaming.assemblyai.com/v3/ws?" + urlencode(PARAMS)


def read_pcm(path):
    with wave.open(path, "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise SystemExit("audio must be 16 kHz mono 16-bit WAV")
        return w.readframes(w.getnframes())


async def run(audio_path, out_path):
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        raise SystemExit("ASSEMBLYAI_API_KEY not set")
    pcm = read_pcm(audio_path)
    step = 16000 * 2 * CHUNK_MS // 1000
    turns = {}  # turn_order -> {"speaker", "text"}
    done = asyncio.Event()
    error = []

    with open(out_path + ".frames.jsonl", "w", encoding="utf-8") as frames:
        async with websockets.connect(
            URL, additional_headers={"Authorization": key}, max_size=None
        ) as ws:

            async def receiver():
                try:
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8")
                        line = raw if "\n" not in raw else json.dumps(json.loads(raw))
                        frames.write(line + "\n")
                        frames.flush()
                        msg = json.loads(raw)
                        t = msg.get("type")
                        if t == "Begin":
                            cfg = msg.get("configuration") or {}
                            if cfg.get("model") != PARAMS["speech_model"]:
                                error.append(f"unexpected model: {cfg.get('model')}")
                        elif t == "Turn" and msg.get("end_of_turn"):
                            turns[msg["turn_order"]] = {
                                "speaker": msg.get("speaker_label") or "UNKNOWN",
                                "text": msg.get("transcript", ""),
                            }
                        elif t == "SpeakerRevision":
                            for r in msg.get("revisions", []):
                                if r.get("turn_order") in turns and r.get("speaker_label"):
                                    turns[r["turn_order"]]["speaker"] = r["speaker_label"]
                        elif t == "Termination":
                            done.set()
                            return
                        elif t == "Error" or "error" in msg:
                            error.append(str(msg))
                            done.set()
                            return
                except websockets.ConnectionClosed as e:
                    if not done.is_set():
                        error.append(f"connection closed early: {e}")
                finally:
                    done.set()

            rx = asyncio.create_task(receiver())
            loop = asyncio.get_running_loop()
            start = loop.time()
            for i, off in enumerate(range(0, len(pcm), step)):
                if done.is_set():
                    break
                await ws.send(pcm[off : off + step])
                delay = start + (i + 1) * CHUNK_MS / 1000 - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
            if not done.is_set():
                await ws.send(json.dumps({"type": "Terminate"}))
                try:
                    await asyncio.wait_for(done.wait(), 30)
                except asyncio.TimeoutError:
                    error.append("timed out waiting for Termination")
            await ws.close()
            await rx

    if error:
        raise SystemExit("; ".join(error))
    result = {"turns": [turns[k] for k in sorted(turns)]}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: transcribe.py <audio.wav> <out.json>")
    asyncio.run(run(*sys.argv[1:]))
