"""CallerAudioRouter: the gate between the caller's voice and the model.

It sits in the pipeline right after the microphone. Every 20 ms of the caller's
audio passes through it. Normally it hands each piece on to the next processor,
so the model hears the caller. During a security check the guard calls
`divert()`, and the router stops handing audio on: nothing further down the
pipeline, including the model, receives it. The pieces go to the recorder
instead, which keeps the caller's answer for the voice check.
"""

from __future__ import annotations

import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .recorder import AnswerRecorder, Capture


class CallerAudioRouter(FrameProcessor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._diverted = False
        self._recorder = AnswerRecorder()
        # Whether audio is playing to the caller, from the frames the speaker end sends back.
        self._bot_speaking = False
        self._bot_started = asyncio.Event()
        self._bot_stopped = asyncio.Event()
        self._bot_stopped.set()

    # --- The gate ---------------------------------------------------------

    @property
    def diverted(self) -> bool:
        return self._diverted

    def divert(self) -> None:
        """Stop the model hearing the caller."""
        self._diverted = True

    def restore(self) -> None:
        """Let the model hear the caller again, and drop any unfinished recording."""
        self._diverted = False
        self._recorder.cancel()

    async def capture(self, **limits) -> Capture:
        """Record the caller's answer. Only allowed while the model is cut off."""
        if not self._diverted:
            raise RuntimeError("recording an answer needs diverted mode")
        return await self._recorder.capture(**limits)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self._recorder.feed(frame.audio, frame.sample_rate, frame.num_channels)
            if not self._diverted:
                await self.push_frame(frame, direction)  # on to the model
            return  # diverted: the piece stops here
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self._bot_stopped.clear()
            self._bot_started.set()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._bot_stopped.set()
        await self.push_frame(frame, direction)

    # --- Waiting for audio to start and stop playing ----------------------

    @property
    def bot_speaking(self) -> bool:
        return self._bot_speaking

    def expect_bot_speech(self) -> None:
        self._bot_started.clear()

    async def wait_bot_started(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._bot_started.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def wait_bot_quiet(self, timeout: float = 30.0) -> None:
        try:
            await asyncio.wait_for(self._bot_stopped.wait(), timeout)
        except asyncio.TimeoutError:
            pass
