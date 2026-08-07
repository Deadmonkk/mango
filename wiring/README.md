# Wiring

Mango is an overlay. Most of it is self-contained, but a few features are
*fallbacks* that only fire once the host project's own modules know to call them —
and those edits live in files this pack does not ship.

`upstream-wiring.patch` captures every such edit as a reproducible diff against
`github.com/fakoli/terminalq` at the commit Mango was developed on.

**Why this file exists.** Those edits previously lived only in one working
directory, untracked. On 2026-08-05 a `git checkout --` on one of those files
destroyed 659 lines of work that no backup held. This patch is the record.

It has since paid for itself. On **2026-08-06** a `git stash` + `reset` in the
host checkout reverted all 17 modified upstream files at once; applying this
patch restored every one of them. Two lessons were folded back in:

- **Never run git commands in the host checkout.** It is someone else's
  repository carrying thousands of lines of uncommitted local work. `checkout`,
  `reset`, `stash` and `clean` are all irreversible there.
- **The path filter now includes root files.** The 2026-08-05 filter covered
  only `src/ tests/ docs/ skills/ commands/`, so `pyproject.toml` and
  `CLAUDE.md` silently fell outside the backup and had to be recovered from a
  stash that happened to still exist. Both are now captured.

## Applying

Mango is standalone and needs none of this to run on its own — `pip install
mango` and it works. This section applies only when driving the host project's
MCP server, whose own modules call into Mango.

```bash
git clone https://github.com/fakoli/terminalq.git
cd terminalq
# Mango installs as its own package now; it is no longer copied over the
# host's tree. The host imports `mango.*` alongside its own `terminalq.*`.
cp -r /path/to/Mango/src/mango  src/mango
cp -r /path/to/Mango/tests/.    tests/
cp -r /path/to/Mango/scripts/.  scripts/
git apply /path/to/Mango/wiring/upstream-wiring.patch
```

The patch repoints the host's own modules at `mango.*` — its `server.py`,
`cache.py`, `coingecko.py`, `historical.py` and `finnhub.py`, plus the tests
that patch those targets by string.

Tests that depend on this wiring are skip-guarded (`tests/_upstream_wiring.py`), so
the suite passes either way — it simply reports fewer integration tests without it.

## Regenerating

From a wired checkout:

```bash
git diff -- src/ tests/ docs/ skills/ commands/ pyproject.toml CLAUDE.md \
  ':(exclude)uv.lock' > /path/to/Mango/wiring/upstream-wiring.patch
```

Keep `pyproject.toml` and `CLAUDE.md` in that list — they are root files and an
earlier version of this command silently excluded them.

`CLAUDE.md.reference` is a copy of the report playbooks (FR/EOD/specialist agents)
that drive this toolkit. Kept here as documentation and as a backup; it is not
applied by the patch.
