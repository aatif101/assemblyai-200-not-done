"""Paths, endpoints and credentials. The key is held in memory only."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .secrets import Scrubber, load_dotenv, secret_values

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
AUDIO = FIXTURES / "audio"
SCRIPTS = FIXTURES / "scripts"
RESULTS = ROOT / "results"
PHASE1 = RESULTS / "phase1"

# The skill revision the probe list was drawn from (AssemblyAI/assemblyai-skill).
SKILL_COMMIT = "f584ec557e97ca9ce8151ec4d3b6e2ec8e3ade75"
SKILL_COMMIT_DATE = "2026-11-04"

ENDPOINTS = {
    "async": "https://api.assemblyai.com",
    "sync": "https://sync.assemblyai.com",
    "dictation": "https://dictation.assemblyai.com",
    "streaming": "wss://streaming.assemblyai.com/v3/ws",
    "streaming_v2": "wss://api.assemblyai.com/v2/realtime/ws",
    "tts": "https://tts.us.assemblyai.com",
}


@dataclass
class Settings:
    api_key: str = field(repr=False)  # never shown in repr / logs
    scrubber: Scrubber = field(repr=False)

    def __repr__(self) -> str:  # belt and braces
        return "Settings(api_key=<hidden>)"


def load_settings(require_key: bool = True) -> Settings:
    dotenv = ROOT / ".env"
    env = {**load_dotenv(dotenv), **os.environ}
    key = env.get("ASSEMBLYAI_API_KEY", "").strip()
    if require_key and not key:
        sys.exit(
            "ASSEMBLYAI_API_KEY is not set. Export it or put it in a git-ignored .env "
            "file at the repo root (see .env.example)."
        )
    secrets = secret_values(dotenv=dotenv)
    if key:
        secrets["ASSEMBLYAI_API_KEY"] = key
    return Settings(api_key=key, scrubber=Scrubber(secrets))
