# Wiring

Mango is an overlay. Most of it is self-contained, but a few features are
*fallbacks* that only fire once the host project's own modules know to call them —
and those edits live in files this pack does not ship.

`upstream-wiring.patch` captures every such edit as a reproducible diff against
`github.com/fakoli/terminalq` at the commit Mango was developed on.

**Why this file exists.** Those edits previously lived only in one working
directory, untracked. On 2026-08-05 a `git checkout --` on one of those files
destroyed 659 lines of work that no backup held. This patch is the record.

## Applying

```bash
git clone https://github.com/fakoli/terminalq.git
cd terminalq
cp -r /path/to/Mango/src/terminalq/*  src/terminalq/
cp -r /path/to/Mango/tests/*          tests/
cp -r /path/to/Mango/scripts          .
git apply /path/to/Mango/wiring/upstream-wiring.patch
```

Tests that depend on this wiring are skip-guarded (`tests/_upstream_wiring.py`), so
the suite passes either way — it simply reports fewer integration tests without it.

## Regenerating

From a wired checkout:

```bash
git diff -- src/ tests/ docs/ skills/ commands/ ':(exclude)uv.lock' \
  > /path/to/Mango/wiring/upstream-wiring.patch
```

`CLAUDE.md.reference` is a copy of the report playbooks (FR/EOD/specialist agents)
that drive this toolkit. Kept here as documentation and as a backup; it is not
applied by the patch.
