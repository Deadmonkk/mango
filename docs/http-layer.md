# Outbound HTTP: one policy, one place

**Decision (2026-08-17):** `mango.core.http` is the canonical outbound-HTTP
layer. Provider modules call `fetch_json()` / `fetch_text()` and never touch
`httpx` response semantics themselves.

## The problem this solves

FR fans out to ~70 external sources. Failures across independent providers
compound multiplicatively, so even a low per-source failure rate makes a
fully-clean run the exception:

| Per-source failure rate | P(all 70 succeed) |
|---|---|
| 1% | 50% |
| 2% | 24% |
| 5% | 3% |

"Something always breaks" was therefore arithmetic, not a broken pipeline — and
adding more sources makes it worse, never better.

But most of those failures were not real outages. Measured on 2026-08-17, the
FRED release-calendar endpoint failed **1 call in 6** with a bare `ReadTimeout`
and succeeded on the very next attempt.

The reason a dropped packet became a permanent gap: **resilience was a property
of each provider rather than of the platform.** Of 19 provider modules, only
three (`coingecko`, `crypto_funding`, `finnhub`) retried anything. A single lost
packet against any of the other 16 rendered as
`data unavailable (source failed)` for the rest of the day.

## Fallbacks and retries are not the same thing

This distinction is the one worth remembering.

- A **fallback** covers *"provider A is wrong, gone, or rate-limited."*
- A **retry** covers *"the packet dropped."*

The pipeline had a reasonable number of the first and almost none of the second,
while its actual failures were overwhelmingly of the second kind.

A corollary, learned the hard way: **a fallback must terminate in a different
dependency.** `get_event_scenarios` was documented as the economic calendar's
fallback, but `event_scenarios.py` imports `fred_ext` and calls the same
`get_release_calendar` — so when FRED was briefly unreachable on 2026-08-17,
both paths returned nothing and §11 rendered empty from a "redundant" pair.

## What the layer does

- Timeout, retry, exponential backoff with jitter, and `Retry-After`.
- **Classified retries.** Retryable: `ConnectTimeout`, `ReadTimeout`,
  connection resets, `429/500/502/503/504`. Not retryable: `400/401/403/404`
  and malformed JSON — no number of attempts fixes them, and hammering a
  premium-walled 403 burns quota that `core/usage_tracker.py` counts against a
  hard free-tier ceiling. `providers/finnhub.py` learned this before the layer
  existed and documents its own never-retry-the-403 rule.
- Jitter is capped *after* it is applied, so `MAX_BACKOFF_SECONDS` is a bound
  rather than a suggestion.

## What it deliberately does NOT do

**It does not swallow errors.** `fetch_json` re-raises the real exception once
attempts are exhausted. Providers already convert exceptions into
`{"error": ...}` payloads, and `scripts/fr_collect.py:safe()` already guarantees
a failed source cannot abort a run — a second layer of swallowing here would
only hide which source died.

The house contract is **degrade loudly and correctly**. This layer's only job is
to stop *transient* blips from becoming degradations in the first place.

## Enforcement

`tests/test_http_convention.py` parses every provider's AST and fails the build
if a module issues a raw request or drives a raw response. The tell is a
provider calling `raise_for_status()` itself.

Note what is **not** banned: constructing an `httpx.AsyncClient`. Connection
pooling is desirable, and the client can be passed to
`fetch_json(..., client=...)` while keeping the retry policy. The first version
of this rule banned client construction and was wrong — it enforced an
implementation detail instead of the architectural boundary.

`ALLOWED_RAW_HTTPX` exempts modules that own a stricter policy of their own
(their own limiter, backoff, or never-retry rule). A companion test asserts
every allowlisted filename still exists, so a rename cannot silently exempt a
module.

## Caching and stale-serve

`cache.get_stale()` reads an entry even when expired and returns its age,
without deleting it — `cache.get()` deletes on expiry, which is why the first
attempt at stale-serve silently could never have worked.

Stale values **must** be labelled with their age at the point of display. An
unlabelled stale value is the silent-wrong-data failure that `cache_guard.py`
exists to prevent. The economic calendar is the first consumer: 6h TTL, and on a
fetch failure it serves the cached schedule tagged `served from cache (Nh old)`
rather than rendering an empty section.

## Explicitly out of scope

- **Circuit breakers.** Measured rather than assumed: the CoinGecko outage on
  2026-08-17 raised `ConnectError`, which fails in milliseconds. The entire
  outage cost ~21s of a ~70s run, most of it sequential fallback attempts, not
  retries against a dead host.
- **SLOs, metrics pipelines, health dashboards.** This is a single-user batch
  job run 10–20x/day, not a service. `core/audit.py` already answers "what did
  the tool return, and when".

## Status of the claim

The tests prove the **mechanism** is correct: a `ReadTimeout` retries and
succeeds, a 403 does not retry, `Retry-After` is honoured, backoff stays
bounded.

They do **not** prove the operational improvement. A post-migration sample of 22
real FRED calls succeeded with no retry ever firing — evidence that FRED was
healthy, not that recovery works in the wild. The visible-failure-count
reduction is a hypothesis until a week of real runs has accumulated.
