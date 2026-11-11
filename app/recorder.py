"""AnswerRecorder: records the caller's answer to a security prompt.

The router feeds it every piece of the caller's audio. It keeps the last 0.3 s
at all times, tracks how loud the room is, and while a capture is running it
records until the caller has finished speaking. Plain Python: no Pipecat here.
"""

from __future__ import annotations

import asyncio
import io
import wave
from collections import deque
from dataclasses import dataclass

import numpy as np

# "Audio stopped playing" arrives a moment late, so a recording starts with the
# last 0.3 s, or the caller's first word could be lost.
PREROLL_SECONDS = 0.3
# Loudness gate limits, in 16-bit sample units.
MIN_THRESHOLD = 40.0
MAX_THRESHOLD = 400.0
MIN_SPEECH_SECONDS = 0.1


def rms(audio: bytes) -> float:
    """Loudness of a piece of 16-bit audio."""
    samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0


def to_wav(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(pcm)
    return buffer.getvalue()


@dataclass
class Capture:
    pcm: bytes
    sample_rate: int
    heard_speech: bool

    def wav(self) -> bytes:
        return to_wav(self.pcm, self.sample_rate)


class _Recording:
    """One answer being recorded. Ends on trailing silence, a time limit, or no speech."""

    def __init__(self, max_seconds, silence_seconds, no_speech_seconds, threshold, preroll, rate):
        self.max_seconds = max_seconds
        self.silence_seconds = silence_seconds
        self.no_speech_seconds = no_speech_seconds
        self.threshold = threshold
        self.buffer = bytearray(preroll)
        self.sample_rate = rate
        self.elapsed = 0.0
        self.voiced = 0.0
        self.trailing_silence = 0.0
        self.future: asyncio.Future[Capture] = asyncio.get_running_loop().create_future()

    @property
    def heard_speech(self) -> bool:
        return self.voiced >= MIN_SPEECH_SECONDS

    def add(self, audio: bytes, level: float, seconds: float) -> None:
        self.buffer += audio
        self.elapsed += seconds
        if level >= self.threshold:
            self.voiced += seconds
            self.trailing_silence = 0.0
        else:
            self.trailing_silence += seconds
        finished = (
            self.elapsed >= self.max_seconds
            or (self.heard_speech and self.trailing_silence >= self.silence_seconds)
            or (not self.heard_speech and self.elapsed >= self.no_speech_seconds)
        )
        if finished and not self.future.done():
            self.future.set_result(Capture(bytes(self.buffer), self.sample_rate, self.heard_speech))


class AnswerRecorder:
    def __init__(self):
        self._preroll: deque[tuple[bytes, float]] = deque()
        self._preroll_length = 0.0
        self._levels: deque[float] = deque(maxlen=150)  # about 3 s of recent loudness
        self._recording: _Recording | None = None
        self._sample_rate = 16000

    def feed(self, audio: bytes, sample_rate: int, channels: int = 1) -> None:
        """Called by the router with every piece of the caller's audio."""
        self._sample_rate = sample_rate
        seconds = len(audio) / 2 / sample_rate / max(channels, 1)
        level = rms(audio)
        self._levels.append(level)
        self._preroll.append((audio, seconds))
        self._preroll_length += seconds
        while self._preroll_length > PREROLL_SECONDS and self._preroll:
            _, dropped = self._preroll.popleft()
            self._preroll_length -= dropped
        if self._recording is not None:
            self._recording.add(audio, level, seconds)

    def _threshold(self) -> float:
        """Loudness gate: 2.5 x the quiet background level. Voice detectors miss
        quiet speech, so loudness is the safer signal."""
        if not self._levels:
            return MIN_THRESHOLD
        ordered = sorted(self._levels)
        floor = ordered[int(len(ordered) * 0.2)]
        return min(MAX_THRESHOLD, max(MIN_THRESHOLD, floor * 2.5))

    async def capture(
        self, max_seconds: float = 10.0, silence_seconds: float = 0.9, no_speech_seconds: float = 8.0
    ) -> Capture:
        preroll = b"".join(chunk for chunk, _ in self._preroll)
        recording = _Recording(
            max_seconds, silence_seconds, no_speech_seconds, self._threshold(), preroll,
            self._sample_rate,
        )
        self._recording = recording
        # The limits count audio pieces. If pieces stop arriving, this clock still ends it.
        deadline = max(max_seconds, no_speech_seconds) + 2.0
        try:
            return await asyncio.wait_for(asyncio.shield(recording.future), deadline)
        except asyncio.TimeoutError:
            return Capture(bytes(recording.buffer), recording.sample_rate, recording.heard_speech)
        finally:
            self._recording = None

    def cancel(self) -> None:
        """Drop an unfinished recording."""
        if self._recording is not None and not self._recording.future.done():
            self._recording.future.cancel()
        self._recording = None
