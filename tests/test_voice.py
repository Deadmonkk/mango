"""Tests for mango.voice — macOS `say` spoken briefings."""

import subprocess
from unittest.mock import MagicMock, patch

from mango import voice


async def test_speak_invokes_say_with_args():
    with (
        patch.object(voice.sys, "platform", "darwin"),
        patch.object(voice.subprocess, "run", return_value=MagicMock()) as mock_run,
    ):
        result = await voice.speak("Regime is mid-cycle.", voice="Samantha")

    assert result["spoken"] is True
    assert result["voice"] == "Samantha"
    assert result["truncated"] is False
    # text passed as args, no shell
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "say"
    assert "-v" in cmd and "Samantha" in cmd
    assert cmd[-1] == "Regime is mid-cycle."


async def test_speak_empty_text():
    result = await voice.speak("   ")
    assert "error" in result


async def test_speak_non_macos():
    with patch.object(voice.sys, "platform", "linux"):
        result = await voice.speak("hello")
    assert "error" in result
    assert "macOS" in result["error"]


async def test_speak_truncates_long_text():
    long_text = "word " * 2000  # well over MAX_SPEAK_CHARS
    with (
        patch.object(voice.sys, "platform", "darwin"),
        patch.object(voice.subprocess, "run", return_value=MagicMock()),
    ):
        result = await voice.speak(long_text)
    assert result["truncated"] is True
    assert result["chars"] == voice.MAX_SPEAK_CHARS


async def test_speak_handles_missing_binary():
    with (
        patch.object(voice.sys, "platform", "darwin"),
        patch.object(voice.subprocess, "run", side_effect=FileNotFoundError()),
    ):
        result = await voice.speak("hello")
    assert "error" in result


async def test_speak_handles_timeout():
    with (
        patch.object(voice.sys, "platform", "darwin"),
        patch.object(voice.subprocess, "run", side_effect=subprocess.TimeoutExpired("say", 180)),
    ):
        result = await voice.speak("hello")
    assert "error" in result
