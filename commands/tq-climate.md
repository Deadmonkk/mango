---
name: tq-climate
description: Climate/weather production-risk watch — commodity regions running hot/cold or wet/dry vs normal
arguments: []
---

Call `terminalq_get_climate_risk_watch`.

Present each tracked region as a short table: Region | Temp Anomaly | Precip Anomaly | Signal. Then, for any region marked FLAGGED, walk the full value chain from its `watch` field — upstream producers/miners, midstream processors/traders, downstream consumer-facing buyers, and other_assets (farmland REITs, country ETFs) — and explain in plain English what a sustained anomaly there would plausibly do at each stage (e.g. a drought flag on Mato Grosso means soybean/corn supply risk — bullish for ZS/ZC futures, a cost headwind for midstream traders ADM/BG, and a cost tailwind risk for downstream livestock-feed buyers like TSN).

State clearly this is a real observed-weather read (NASA POWER/MERRA-2), not an ENSO/El Nino index — never call a flagged region "El Nino" without separately checking NOAA's ONI status. Note coverage is curated (grains/oilseeds, softs, copper/lithium, Permian oil/gas) — not exhaustive of every commodity — and every ticker was verified against a live search, not assumed from memory (flag if any name looks stale, e.g. after an acquisition). Close with which flagged regions, if any, are worth cross-checking against COT positioning (`terminalq_get_cot_report`) or spot commodity prices (`terminalq_get_commodities`) before treating this as more than a watch-item.
