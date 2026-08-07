# TerminalQ → Mango migration

Goal: Mango replaces the host project entirely on this machine, losing no
functionality, and the host checkout is then removed. Nothing is deleted until
the replacement is verified live.

## Scope, measured 2026-08-07

| | Count |
|---|---|
| MCP tools | 84 |
| …already fully served by Mango | 35 |
| …needing a host module | 49 |
| Host modules with no Mango equivalent | 12 |
| `server.py` | 1,582 lines, host-derived — must be rewritten |
| Slash commands | 39 (27 host-owned) |
| Skills | 6 (all host-owned) |
| Hooks | 4 (all host-owned) |

**The commands, skills and hooks are tracked in the host's git**, so copying them
would reintroduce the licensing problem the independence work removed. Anything
kept must be rewritten clean-room.

## Owner decisions (2026-08-07)

- **Skills: keep all six.** Rewrite clean-room.
- **Slash commands: not used much — do not port wholesale.** Where a command
  encodes something genuinely useful, express it as a *skill* instead. A skill
  carries reasoning and can do more than a prompt stub, so this is a
  simplification rather than a compromise.
- **All 84 tools: keep.** No functionality loss.

## Stages

1. **Providers** — port the 12 missing modules clean-room.
2. **MCP server** — write Mango's own, exposing all 84 tools.
3. **Skills** — rewrite the six; fold in worthwhile command behaviour.
4. **Cut over** — repoint `~/.claude.json`, verify all tools live over stdio,
   run FR and EOD end to end.
5. **Remove the host** — only after stage 4 passes, and after a full backup.

## Status — paused 2026-08-07

Nothing is wired. The host project is untouched and fully working; every new
module below is additive and imported by nothing, so the running system cannot
be affected by this work in its current state.

| Provider | Tools | State |
|---|---|---|
| finnhub | 9 | **complete** — 542 lines, 18 tests passing |
| edgar | 4 | module written (873 lines), **tests missing** — agent paused mid-task |
| coingecko full API | 8 | module written (782 lines), **tests missing** — agent paused mid-task |
| charts | 5 | module written (328 lines), **tests missing** — agent paused mid-task |
| risk, allocation | 3 | **not started** — same agent, did not reach them |
| technical, screener, search | 3 | not started |
| audit, usage_tracker, config | — | not started |
| server.py (84 tools) | — | stage 2, not started |

Coverage so far: **9 of 49** host-dependent tools have a verified replacement;
another 17 have unverified modules.

## Resuming

The three modules with no tests are **unverified**. Do not trust them because
they exist — the contract risk is real (renamed keys silently break report
sections). For each: write the tests from the saved payloads at
`~/Desktop/TerminalIQ Reports/.briefs/fr_raw_*.json`, then verify the shape
matches what consumers read, exactly as was done for finnhub.

Specific contracts to re-check when resuming:

- `coingecko.get_crypto_deep` → callers read `price.usd`, `returns.24h/7d/30d`,
  all-time-high block.
- `coingecko` high-level functions must return `{"error": ...}`, NOT the
  low-level `{"_error": ...}`. The `_error` spelling is what `crypto_analytics`
  tests to trigger its Yahoo fallback — getting it backwards disables the
  fallback silently and no test would notice.
- `edgar.get_insider_transactions` → `{symbol, company_name, transactions:[
  {date, owner, title, transaction_type, shares, price, value}]}`; `price` and
  `value` legitimately parse as 0.0 in real filings — pass through, never fill in.

Verification standard for this migration, learned the hard way this week: an
import check is not a start check, and a module existing is not a module
working. Before stage 5 removes anything, all 84 tools must respond over stdio
from Mango's own server.

## Do not delete the host yet

`~/Projects/terminalq` still carries ~3,600 lines of local modification, captured
in `wiring/upstream-wiring.patch`. It remains the only working MCP server. Run
no git command in that directory.
