"""The recorder: keeps the answer, ends after the caller stops speaking."""

import asyncio

import numpy as np

from app.recorder import AnswerRecorder

RATE = 16000
PIECE = int(RATE * 0.02)  # 20 ms, like a Pipecat audio frame


def piece(level: int) -> bytes:
    return (np.ones(PIECE, dtype=np.int16) * level).tobytes()


async def test_records_speech_and_stops_after_silence():
    recorder = AnswerRecorder()
    for _ in range(50):  # a quiet room while the prompt plays
        recorder.feed(piece(10), RATE)
    capture = asyncio.create_task(recorder.capture(silence_seconds=0.9))
    await asyncio.sleep(0)
    for _ in range(50):  # 1 s of speech
        recorder.feed(piece(3000), RATE)
    for _ in range(50):  # 1 s of quiet ends it after 0.9 s
        recorder.feed(piece(10), RATE)
    result = await capture
    assert result.heard_speech
    # 0.3 s pre-roll + 1 s speech + 0.9 s silence
    assert abs(len(result.pcm) / 2 / RATE - 2.2) < 0.05


async def test_silence_only_is_reported_as_no_speech():
    recorder = AnswerRecorder()
    capture = asyncio.create_task(recorder.capture(no_speech_seconds=1.0))
    await asyncio.sleep(0)
    for _ in range(60):
        recorder.feed(piece(10), RATE)
    assert not (await capture).heard_speech
