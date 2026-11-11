"""The guard: the model can't hear the answer, and audio always comes back."""

import re

from app.recorder import Capture
from app.guard import Guard, SecurityVoiceError
from app.voice_key import CheckResult


class FakeRouter:
    def __init__(self, answers: list[bool]):
        self.answers = answers  # whether each recording heard speech
        self.diverted = False
        self.bot_speaking = False
        self.diverted_while_recording: list[bool] = []

    def divert(self):
        self.diverted = True

    def restore(self):
        self.diverted = False

    async def capture(self, **_):
        self.diverted_while_recording.append(self.diverted)
        return Capture(b"\x00\x00" * 16000, 16000, self.answers.pop(0))


class FakeModel:
    """Stands in for OpenAIRealtimeLLMService: the two calls the guard makes."""

    def __init__(self):
        self.paused = False
        self.paused_while_speaking: list[bool] = []
        self.cleared = False

    def set_audio_input_paused(self, paused):
        self.paused = paused

    async def send_client_event(self, event):
        self.cleared = True


class FakeSettings:
    speaker_min = 0.35
    spoof_max = 0.5


class FakeVoiceKey:
    settings = FakeSettings()

    def __init__(self, exists=True, passes=True):
        self.exists = exists
        self.passes = passes
        self.codes: list[str] = []

    def check(self, clip, code):
        self.codes.append(code)
        return CheckResult(failed=[] if self.passes else ["x"], speaker=0.7, spoof=0.02, heard=f"code {code}")


class GuardWithFakeVoice(Guard):
    """The real guard, with the security voice replaced: it records what it would say."""

    def __init__(self, *args, voice_fails=False):
        super().__init__(*args)
        self.voice_fails = voice_fails
        self.said: list[str] = []
        self.paused_while_speaking: list[bool] = []

    async def _say(self, text):
        if self.voice_fails:
            raise SecurityVoiceError("no audio")
        self.paused_while_speaking.append(self._llm.paused)
        self.said.append(text)

    async def _beep(self):
        pass

    async def _wait_until_quiet(self):
        pass


def make(answers=(True,), exists=True, passes=True, voice_fails=False):
    router, model, key, events = FakeRouter(list(answers)), FakeModel(), FakeVoiceKey(exists, passes), []

    async def notify(event):
        events.append(event)

    async def queue(frames):
        pass

    guard = GuardWithFakeVoice(router, model, key, queue, notify, voice_fails=voice_fails)
    return guard, router, model, guard, key, events


async def test_the_answer_is_recorded_while_the_model_is_cut_off():
    guard, router, model, voice, key, _ = make()
    result = await guard.verify("Send £40.00 to Sam")
    assert result.passed
    assert router.diverted_while_recording == [True]
    assert voice.paused_while_speaking == [True]
    assert not router.diverted and not model.paused  # audio is back afterwards


async def test_the_code_checked_is_the_code_spoken():
    guard, _, _, voice, key, _ = make()
    await guard.verify("Send £40.00 to Sam")
    spoken = "".join(re.findall(r"\d", voice.said[0].split("code")[-1]))
    assert len(key.codes[0]) == 4 and spoken == key.codes[0]


async def test_the_caller_hears_what_they_are_approving():
    guard, _, _, voice, _, _ = make()
    await guard.verify("Send £40.00 to Sam Patel")
    assert "You asked to send £40.00 to Sam Patel." in voice.said[0]


async def test_a_new_code_every_time():
    guard, _, _, _, key, _ = make(answers=[True] * 8)
    for _ in range(8):
        await guard.verify("Send £40.00 to Sam")
    assert len(set(key.codes)) > 1


async def test_no_voice_key_fails_without_a_challenge():
    guard, router, model, voice, _, _ = make(exists=False)
    result = await guard.verify("Send £40.00 to Sam")
    assert not result.passed and result.failed == ["no voice key set up"]
    assert voice.said == [] and router.diverted_while_recording == []


async def test_silence_gets_one_more_try_then_fails():
    guard, _, _, voice, key, _ = make(answers=[False, False])
    result = await guard.verify("Send £40.00 to Sam")
    assert not result.passed and result.failed == ["no answer heard"]
    assert len(voice.said) == 2 and key.codes == []


async def test_a_broken_security_voice_fails_closed_and_gives_audio_back():
    guard, router, model, _, key, _ = make(voice_fails=True)
    result = await guard.verify("Send £40.00 to Sam")
    assert not result.passed and key.codes == []
    assert not router.diverted and not model.paused


async def test_the_browser_sees_each_stage():
    guard, *_, events = make()
    await guard.verify("Send £40.00 to Sam")
    stages = [event["stage"] for event in events]
    assert stages == ["prompt", "listening", "checking", "result", "done"]
