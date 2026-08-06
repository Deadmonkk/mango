"""Spoken briefings via the macOS built-in `say` command (free, no API key).

Lets you *listen* to a market read over coffee instead of reading twelve sections.
Uses `say` directly — no fal.ai, no key, no per-use cost. Text is passed as a
process argument (never through a shell), so there is no command-injection
surface. Returns the provider error-dict convention; never raises.
"""

import asyncio
import subprocess
import sys

from terminalq.mango.logging import log

MAX_SPEAK_CHARS = 4000  # ~5 minutes of speech; keep briefings tight
SPEAK_TIMEOUT_S = 180


async def speak(text: str, voice: str = "") -> dict:
    """Read text aloud through macOS `say`.

    Args:
        text: The briefing text to speak.
        voice: Optional `say` voice name (e.g. "Samantha", "Daniel"). Default voice if blank.

    Returns:
        Dict confirming what was spoken, or an error dict (non-macOS, missing
        binary, timeout). Never raises.
    """
    text = (text or "").strip()
    if not text:
        return {"error": "No text to speak", "source": "voice (say)"}
    if sys.platform != "darwin":
        return {"error": "Spoken briefings require macOS (`say` command)", "source": "voice (say)"}

    truncated = len(text) > MAX_SPEAK_CHARS
    spoken_text = text[:MAX_SPEAK_CHARS]

    cmd = ["say"]
    if voice:
        cmd += ["-v", voice]
    cmd.append(spoken_text)

    try:
        await asyncio.to_thread(subprocess.run, cmd, check=True, capture_output=True, timeout=SPEAK_TIMEOUT_S)
    except FileNotFoundError:
        return {"error": "`say` command not found", "source": "voice (say)"}
    except subprocess.TimeoutExpired:
        log.warning("say timed out")
        return {"error": "Speech timed out", "source": "voice (say)"}
    except subprocess.CalledProcessError as e:
        log.warning("say failed: %s", e)
        return {"error": "say command failed", "source": "voice (say)"}

    return {
        "spoken": True,
        "chars": len(spoken_text),
        "truncated": truncated,
        "voice": voice or "system default",
        "source": "voice (say)",
    }
