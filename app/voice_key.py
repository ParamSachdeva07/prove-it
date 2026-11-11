"""The voice key: save a voice once, then check live answers against it.

Three off-the-shelf models do the work. This file only loads them and combines
their scores.

- SpeechBrain ECAPA-TDNN turns speech into a voiceprint (192 numbers) and
  compares two voiceprints with cosine similarity.
- DF Arena 500M scores how likely speech is to be synthetic.
- faster-whisper transcribes the answer, to check the one-time code.
"""

from __future__ import annotations

import io
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import soundfile as sf
import soxr
from loguru import logger

from .config import (
    DATA_DIR,
    DF_ARENA_REPO,
    DF_ARENA_REVISION,
    ECAPA_REPO,
    ECAPA_REVISION,
    MODELS_DIR,
    WHISPER_REPO,
    WHISPER_REVISION,
    Settings,
)
from .digits import said_code, spoken_digits

SR = 16000
SPOOF_WINDOW = 64600  # DF Arena looks at exactly this many samples (about 4 s)
SPOOF_MAX_WINDOWS = 3
CHECK_MIN_SECONDS = 1.0


def load_wav(data: bytes) -> np.ndarray:
    """Any WAV to 16 kHz mono float32."""
    audio, rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if rate != SR:
        audio = soxr.resample(audio, rate, SR)
    return np.ascontiguousarray(audio, dtype=np.float32)


def trim_silence(audio: np.ndarray, below_peak_db: float = -35.0, pad: float = 0.1) -> np.ndarray:
    """Cuts the quiet start and end, so the voiceprint is made from speech."""
    frame, hop = 400, 160
    if len(audio) < frame:
        return audio
    count = 1 + (len(audio) - frame) // hop
    index = np.arange(frame)[None, :] + hop * np.arange(count)[:, None]
    level = np.sqrt(np.mean(audio[index] ** 2, axis=1)) + 1e-9
    loud = np.nonzero(level >= np.percentile(level, 95) * 10 ** (below_peak_db / 20))[0]
    if loud.size == 0:
        return audio[:0]
    start = max(0, loud[0] * hop - int(pad * SR))
    end = min(len(audio), loud[-1] * hop + frame + int(pad * SR))
    return audio[start:end]


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a, b = np.ravel(a).astype(np.float64), np.ravel(b).astype(np.float64)
    norms = np.linalg.norm(a) * np.linalg.norm(b)
    return float(a @ b / norms) if norms else 0.0


def spoof_windows(audio: np.ndarray) -> list[np.ndarray]:
    """Up to three 4 s windows. Short clips are repeated to fill one, as the model expects."""
    if len(audio) == 0:
        return [np.zeros(SPOOF_WINDOW, np.float32)]
    if len(audio) <= SPOOF_WINDOW:
        return [np.tile(audio, SPOOF_WINDOW // len(audio) + 1)[:SPOOF_WINDOW]]
    starts = list(range(0, len(audio) - SPOOF_WINDOW + 1, SPOOF_WINDOW))[:SPOOF_MAX_WINDOWS]
    return [audio[s : s + SPOOF_WINDOW] for s in starts]


@dataclass
class CheckResult:
    # Required, so a result can't pass by accident: an empty list is the only way to pass.
    failed: list[str]
    speaker: float = 0.0
    spoof: float = 0.0
    heard: str = ""
    speaker_ok: bool = False
    spoof_ok: bool = False
    code_ok: bool = False

    @property
    def passed(self) -> bool:
        return not self.failed

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "passed": self.passed}


class VoiceKey:
    def __init__(self, settings: Settings, key_path=None):
        self.settings = settings
        self.key_path = key_path or DATA_DIR / "voice_key.npy"
        self.ready = False
        self.error: str | None = None
        self._lock = threading.Lock()  # the models aren't safe to run in parallel

    # --- Loading ----------------------------------------------------------

    def load(self) -> None:
        try:
            self._load()
            self.ready = True
            logger.info(f"voice models ready on {self.device}")
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            logger.exception("voice models failed to load")

    def _load(self) -> None:
        import torch
        from faster_whisper import WhisperModel
        from huggingface_hub import snapshot_download
        from speechbrain.inference.speaker import SpeakerRecognition
        from transformers import AutoModel

        self.device = "mps" if torch.backends.mps.is_available() else "cpu"

        ecapa = snapshot_download(
            ECAPA_REPO, revision=ECAPA_REVISION, allow_patterns=["*.yaml", "*.ckpt"]
        )
        self.speaker = SpeakerRecognition.from_hparams(
            source=ecapa,
            savedir=str(MODELS_DIR / "speechbrain-ecapa"),
            run_opts={"device": self.device},
        )
        try:
            self._embed(np.zeros(SR, np.float32) + 0.01)
        except Exception:  # noqa: BLE001
            # Some layers aren't supported on Apple GPUs in every torch version.
            self.device = "cpu"
            self.speaker = SpeakerRecognition.from_hparams(
                source=ecapa,
                savedir=str(MODELS_DIR / "speechbrain-ecapa"),
                run_opts={"device": "cpu"},
            )

        self.spoof_model = AutoModel.from_pretrained(
            DF_ARENA_REPO, revision=DF_ARENA_REVISION, trust_remote_code=True
        )
        self.spoof_model = self.spoof_model.eval().to(self.device)

        whisper = snapshot_download(WHISPER_REPO, revision=WHISPER_REVISION)
        # faster-whisper has no Apple GPU support. int8 on the CPU is fast enough here.
        self.whisper = WhisperModel(whisper, device="cpu", compute_type="int8", cpu_threads=4)

    # --- The three models -------------------------------------------------

    def _embed(self, audio: np.ndarray) -> np.ndarray:
        import torch

        with torch.inference_mode():
            wav = torch.from_numpy(audio).unsqueeze(0).to(self.device)
            return self.speaker.encode_batch(wav).squeeze().float().cpu().numpy()

    def _spoof(self, audio: np.ndarray) -> float:
        """Probability the speech is synthetic. The most suspicious window counts."""
        import torch

        scores = []
        with torch.inference_mode():
            for window in spoof_windows(audio):
                x = torch.from_numpy(np.ascontiguousarray(window)).to(self.device)
                logits = self.spoof_model(x)["logits"]
                scores.append(float(torch.softmax(logits.float(), dim=-1)[0, 0].cpu()))
        return max(scores)

    def _transcribe(self, audio: np.ndarray) -> str:
        # No hint of the expected words, or Whisper would lean towards hearing them.
        segments, _ = self.whisper.transcribe(
            audio, language="en", beam_size=5, condition_on_previous_text=False
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    # --- Voice key --------------------------------------------------------

    @property
    def exists(self) -> bool:
        return self.key_path.exists()

    def delete(self) -> None:
        self.key_path.unlink(missing_ok=True)

    def enrol(self, clips: list[bytes]) -> dict[str, Any]:
        """Each sentence must be long enough and not synthetic. Then all of them
        together become one voiceprint, saved as the key."""
        speech: list[np.ndarray] = []
        results: list[dict[str, Any]] = []
        with self._lock:
            for clip in clips:
                audio = trim_silence(load_wav(clip))
                seconds = len(audio) / SR
                spoof = self._spoof(audio) if seconds > 0 else 1.0
                problem = None
                if seconds < self.settings.enrol_min_seconds:
                    problem = "too short"
                elif spoof >= self.settings.spoof_max:
                    problem = "sounds synthetic"
                results.append(
                    {"seconds": round(seconds, 1), "spoof": round(spoof, 3), "problem": problem}
                )
                speech.append(audio)
            if any(item["problem"] for item in results):
                return {"ok": False, "clips": results}
            voiceprint = self._embed(np.concatenate(speech))
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(self.key_path, voiceprint)
        return {"ok": True, "clips": results}

    def check(self, clip: bytes, code: str) -> CheckResult:
        """Same voice as the key, not synthetic, and the right code."""
        audio = trim_silence(load_wav(clip))
        if len(audio) / SR < CHECK_MIN_SECONDS:
            return CheckResult(failed=["too short"])
        key = np.load(self.key_path)
        with self._lock:
            speaker = cosine(key, self._embed(audio))
            spoof = self._spoof(audio)
            heard = self._transcribe(audio)
        speaker_ok = speaker >= self.settings.speaker_min
        spoof_ok = spoof < self.settings.spoof_max
        code_ok = said_code(heard, code)
        failed = []
        if not speaker_ok:
            failed.append("voice doesn't match")
        if not spoof_ok:
            failed.append("sounds synthetic")
        if not code_ok:
            failed.append(f"wrong code (heard {spoken_digits(heard) or 'none'})")
        return CheckResult(
            failed=failed,
            speaker=round(speaker, 3),
            spoof=round(spoof, 3),
            heard=heard,
            speaker_ok=speaker_ok,
            spoof_ok=spoof_ok,
            code_ok=code_ok,
        )
