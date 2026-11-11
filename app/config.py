"""Settings, read once from .env. Importing this first sets up HTTPS certificates
and points model downloads at ./models."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import truststore
from dotenv import load_dotenv

# Check HTTPS certificates (OpenAI, Cartesia, Hugging Face) against the
# operating system's trust store. The python.org macOS build ships with none,
# so every secure connection would fail without this.
truststore.inject_into_ssl()

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

MODELS_DIR = ROOT / "models"
DATA_DIR = ROOT / "data"
os.environ.setdefault("HF_HOME", str(MODELS_DIR / "hf"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# Pinned model versions, so results don't change under you.
ECAPA_REPO = "speechbrain/spkrec-ecapa-voxceleb"
ECAPA_REVISION = "0f99f2d0ebe89ac095bcc5903c4dd8f72b367286"
DF_ARENA_REPO = "Speech-Arena-2025/DF_Arena_500M_V_1"
DF_ARENA_REVISION = "8258fa8e74ff9b8ad20d4c939c1a7f694a6e4080"
WHISPER_REPO = "Systran/faster-whisper-base.en"
WHISPER_REVISION = "3d3d5dee26484f91867d81cb899cfcf72b96be6c"


@dataclass(frozen=True)
class Settings:
    openai_key: str = os.environ.get("OPENAI_API_KEY", "")
    realtime_model: str = os.environ.get("REALTIME_MODEL", "gpt-realtime-2.1")
    realtime_voice: str = os.environ.get("REALTIME_VOICE", "marin")
    cartesia_key: str = os.environ.get("CARTESIA_API_KEY", "")
    cartesia_voice: str = os.environ.get("CARTESIA_VOICE_ID", "")
    # A live answer passes only if all three hold.
    speaker_min: float = float(os.environ.get("SPEAKER_MIN", "0.35"))
    spoof_max: float = float(os.environ.get("SPOOF_MAX", "0.50"))
    # Each voice key sentence needs this much speech after trimming silence.
    enrol_min_seconds: float = float(os.environ.get("ENROL_MIN_SECONDS", "2.5"))

    @property
    def can_call(self) -> bool:
        return bool(self.openai_key and self.cartesia_key and self.cartesia_voice)


settings = Settings()
