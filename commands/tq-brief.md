---
name: tq-brief
description: Spoken market briefing — listen to the regime read instead of reading it
arguments:
  - name: voice
    description: "Optional macOS voice name (e.g. Samantha, Daniel). Default voice if blank."
    required: false
---

Produce a short **spoken-style** market briefing and read it aloud.

1. Pull the current picture: call `terminalq_load_recent_reports` (n=2) for the latest synthesis + snapshot trend. If no reports exist yet, use the latest `terminalq_record_snapshot` data or run a quick read of the key tools.
2. Write a tight **≤45-second** script (roughly 110-130 words) in plain spoken English — no tables, no markdown, no symbols read awkwardly. Lead with the two regime scores and their direction, then the 2-3 things that actually matter today and the one thing to watch. Conversational, not a data dump.
3. Call `terminalq_speak` with that script (pass `voice_name` = "$ARGUMENTS" if a voice was given).
4. Also print the script text so the user can read along.

Keep every number sourced from the tool results — never invent a figure just to make the briefing flow.
