---
name: tq-graph-reports
description: Build a knowledge graph of the FR report archive to find recurring patterns
arguments: []
---

Run the **graphify** skill on the saved Full Report archive (set `$TERMINALQ_REPORTS_DIR`, default `~/market-reports/`) to turn report *history* into a navigable knowledge graph.

Invoke the graphify skill with the reports directory as the path. Once built, it surfaces cross-report structure the eye misses reading one report at a time — which metrics co-move, which catalysts recurred before regime-score flips, and how the narrative threads connect across weeks. Query it with `graphify query "<question>"`.

**Note on value vs cost:** this uses LLM extraction (consumes Claude plan usage, not dollars), and it is most useful once the archive has real depth — roughly **10+ reports**. With only a handful saved, prefer `/tq-week` (the structured weekly digest) for now and run this once history has accumulated. Tell the user the current report count and let them decide whether to proceed.
