"""The real models on macOS synthetic voices. Run with: uv run pytest -m slow

These voices are synthetic, so the spoof check should catch them. That's the
point of the last test. The others relax the spoof limit to test the rest.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import Settings
from app.voice_key import VoiceKey

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("say"), reason="needs macOS say"),
]

SENTENCES = (
    "Seven bright kites drifted over the harbour while the evening ferry pulled away.",
    "My voice is my own, and I am setting up this key on a quiet afternoon.",
    "Please check the river bridge and the old clock tower before the market closes.",
)


def speak(voice: str, text: str, folder: Path) -> bytes:
    path = folder / f"{voice}-{abs(hash(text))}.wav"
    subprocess.run(
        ["say", "-v", voice, "-o", str(path), "--file-format=WAVE", "--data-format=LEI16@16000", text],
        check=True,
    )
    return path.read_bytes()


@pytest.fixture(scope="module")
def models(tmp_path_factory):
    key = VoiceKey(Settings(spoof_max=1.01), tmp_path_factory.mktemp("key") / "voice_key.npy")
    key.load()
    assert key.ready, key.error
    return key


@pytest.fixture(scope="module")
def enrolled(models, tmp_path_factory):
    folder = tmp_path_factory.mktemp("audio")
    outcome = models.enrol([speak("Daniel", s, folder) for s in SENTENCES])
    assert outcome["ok"], outcome
    return models, folder


def test_the_same_voice_with_the_right_code_passes(enrolled):
    key, folder = enrolled
    result = key.check(speak("Daniel", "I approve this request, code 4, 7, 1, 9.", folder), "4719")
    assert result.speaker >= 0.35 and result.code_ok and result.passed, result


def test_a_different_voice_fails(enrolled):
    key, folder = enrolled
    result = key.check(speak("Samantha", "I approve this request, code 4, 7, 1, 9.", folder), "4719")
    assert result.speaker < 0.35 and not result.passed, result


def test_the_wrong_code_fails(enrolled):
    key, folder = enrolled
    result = key.check(speak("Daniel", "I approve this request, code 1, 2, 3, 4.", folder), "4719")
    assert not result.code_ok and not result.passed, result


def test_synthetic_speech_fails_at_the_normal_limit(enrolled):
    key, folder = enrolled
    strict = VoiceKey(Settings(), key.key_path)
    strict.speaker, strict.spoof_model, strict.whisper, strict.device = (
        key.speaker, key.spoof_model, key.whisper, key.device,
    )
    result = strict.check(speak("Daniel", "I approve this request, code 4, 7, 1, 9.", folder), "4719")
    assert result.spoof >= 0.5 and "sounds synthetic" in result.failed, result
