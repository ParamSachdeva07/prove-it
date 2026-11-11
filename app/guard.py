"""The security check that runs inside a protected tool call.

1. Stop the model hearing the caller: divert the audio and pause the model's input.
2. A separate voice (Cartesia) reads back the request and a one-time code.
   The model never sees it.
3. Record the caller's answer.
4. Check the answer against the voice key: same voice, not synthetic, right code.
5. Give the audio back to the model, whatever happened.
"""

from __future__ import annotations

import asyncio
import math
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from pipecat.frames.frames import Frame, OutputAudioRawFrame, TTSSpeakFrame
from pipecat.services.openai.realtime.events import InputAudioBufferClearEvent
from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService

from .recorder import Capture
from .router import CallerAudioRouter
from .voice_key import CheckResult, VoiceKey

Queue = Callable[[list[Frame]], Awaitable[None]]
Notify = Callable[[dict[str, Any]], Awaitable[None]]

BEEP_RATE = 24000
BEEP_SECONDS = 0.25
CODE_DIGITS = 4


def beep_pcm(sample_rate: int = BEEP_RATE, hz: float = 880.0, seconds: float = BEEP_SECONDS) -> bytes:
    """A short tone that tells the caller to speak, faded in and out so it doesn't click."""
    count = int(sample_rate * seconds)
    fade = max(1, int(sample_rate * 0.01))
    samples = bytearray()
    for i in range(count):
        envelope = min(1.0, i / fade, (count - i) / fade)
        value = int(9000 * envelope * math.sin(2 * math.pi * hz * i / sample_rate))
        samples += value.to_bytes(2, "little", signed=True)
    return bytes(samples)


class SecurityVoiceError(Exception):
    """The prompt couldn't be spoken, so the check can't go ahead."""


class Guard:
    def __init__(
        self,
        router: CallerAudioRouter,
        llm: OpenAIRealtimeLLMService,
        voice_key: VoiceKey,
        queue: Queue,
        notify: Notify,
    ):
        self._router = router
        self._llm = llm
        self._voice_key = voice_key
        self._queue = queue  # puts frames into the call's pipeline (used to speak)
        self._notify = notify  # sends events to the browser
        self._lock = asyncio.Lock()  # the model may call tools in parallel; one check at a time

    async def verify(self, request: str) -> CheckResult:
        if not self._voice_key.exists:
            result = CheckResult(failed=["no voice key set up"])
            await self._stage(request, "result", **result.as_dict())
            return result
        async with self._lock:
            self._router.divert()
            self._llm.set_audio_input_paused(True)
            try:
                # Drop any half-finished caller sentence so it can't become a turn later.
                await self._llm.send_client_event(InputAudioBufferClearEvent())
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"could not clear the model's input: {exc}")
            try:
                return await self._challenge(request)
            except SecurityVoiceError as exc:
                logger.error(f"security voice failed: {exc}")
                return CheckResult(failed=["security voice unavailable"])
            except Exception:  # noqa: BLE001
                logger.exception("security check failed")
                return CheckResult(failed=["check failed"])
            finally:
                self._llm.set_audio_input_paused(False)
                self._router.restore()
                await self._stage(request, "done")

    async def _challenge(self, request: str) -> CheckResult:
        code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
        # The request is read out from the real arguments, so the caller hears what
        # they're approving in this voice, not the model's.
        prompt = (
            f"Security check. You asked to {request[:1].lower()}{request[1:]}. "
            f"If that's right, after the beep say: I approve this request, code {', '.join(code)}."
        )
        await self._wait_until_quiet()

        capture = await self._ask(request, prompt) or await self._ask(
            request, f"I didn't catch that. {prompt}"
        )
        if capture is None:
            result = CheckResult(failed=["no answer heard"])
        else:
            await self._stage(request, "checking")
            result = await asyncio.to_thread(self._voice_key.check, capture.wav(), code)

        logger.info(
            f"security check for {request!r}: passed={result.passed} speaker={result.speaker} "
            f"spoof={result.spoof} code_ok={result.code_ok} heard={result.heard!r} failed={result.failed}"
        )
        limits = self._voice_key.settings
        await self._stage(
            request,
            "result",
            code=code,
            limits={"speaker_min": limits.speaker_min, "spoof_max": limits.spoof_max},
            **result.as_dict(),
        )
        return result

    async def _ask(self, request: str, text: str) -> Capture | None:
        """Say the prompt, beep, and record. Returns the answer, or None if there was silence."""
        await self._stage(request, "prompt", text=text)
        await self._say(text)
        await self._beep()
        await self._stage(request, "listening", text=text)
        capture = await self._router.capture(max_seconds=10.0, silence_seconds=0.9)
        return capture if capture.heard_speech else None

    async def _stage(self, request: str, stage: str, **details: Any) -> None:
        """Tells the browser how the check is going."""
        await self._notify({"type": "security", "stage": stage, "request": request, **details})

    # --- The security voice: exact text through Cartesia, never through the model ---

    async def _say(self, text: str) -> None:
        # append_to_context=False keeps the code out of the model's context.
        # interruptible=False stops a late turn event cutting the prompt off.
        frame = TTSSpeakFrame(text, append_to_context=False)
        frame.interruptible = False
        self._router.expect_bot_speech()
        await self._queue([frame])
        if not await self._router.wait_bot_started(timeout=8.0):
            raise SecurityVoiceError("no audio from the security voice")
        await self._router.wait_bot_quiet(timeout=max(20.0, len(text) / 10))

    async def _beep(self) -> None:
        # Pipecat doesn't count a plain audio frame as bot speech, so there's no
        # "stopped" event to wait for. The beep's length is known instead.
        frame = OutputAudioRawFrame(audio=beep_pcm(), sample_rate=BEEP_RATE, num_channels=1)
        await self._queue([frame])
        await asyncio.sleep(BEEP_SECONDS + 0.05)

    async def _wait_until_quiet(self) -> None:
        """The model may still be finishing a sentence when it calls the tool."""
        if self._router.bot_speaking:
            await self._router.wait_bot_quiet(timeout=20.0)
