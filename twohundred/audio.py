"""Small WAV helpers (stdlib only). All fixtures are 16 kHz mono signed 16-bit PCM."""
from __future__ import annotations

import hashlib
import io
import wave
from pathlib import Path

SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2  # bytes


def read_wav(path: Path) -> tuple[bytes, int, int]:
    """Return (pcm_bytes, sample_rate, channels)."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != SAMPLE_WIDTH:
            raise ValueError(f"{path}: expected 16-bit audio, got {8 * w.getsampwidth()}-bit")
        return w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()


def wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def write_wav(path: Path, pcm: bytes, sample_rate: int = SAMPLE_RATE, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav_bytes(pcm, sample_rate, channels))


def silence(seconds: float, sample_rate: int = SAMPLE_RATE) -> bytes:
    return b"\x00\x00" * int(seconds * sample_rate)


def duration_s(pcm: bytes, sample_rate: int = SAMPLE_RATE, channels: int = 1) -> float:
    return len(pcm) / (SAMPLE_WIDTH * channels * sample_rate)


def chunks(pcm: bytes, ms: int = 100, sample_rate: int = SAMPLE_RATE):
    """Yield fixed-duration PCM chunks (the last one may be shorter)."""
    step = int(sample_rate * ms / 1000) * SAMPLE_WIDTH
    for i in range(0, len(pcm), step):
        yield pcm[i : i + step]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
