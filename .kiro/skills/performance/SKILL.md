---
name: performance
description: Performance engineering - profiling, benchmarking, load testing, and optimization across backend and frontend (Core Web Vitals). Use when something is slow, before optimizing, or when setting performance budgets.
---

# Performance

## The iron law: measure, then optimize
No optimization without a measured baseline. "This should be faster" is a hypothesis, not a
result. Profile under realistic conditions, find the *proven* hotspot, fix it, and re-measure to
prove the win. If the gain isn't significant, revert. Optimizing the wrong thing is wasted work
plus new complexity.

## Method
1. **Baseline** — reproducible measurement under realistic load. Record environment, inputs, and
   variance. Warm up; run enough iterations for statistical validity.
2. **Profile** — flamegraphs, sampling profilers, DB query plans, traces. Let evidence point to
   the hotspot; don't guess.
3. **Attribute** — is it CPU, memory, I/O, network, lock contention, or an N+1? Quantify the cost.
4. **Fix the critical path** — algorithmic and architectural wins first (better complexity,
   caching, batching, indexing) before micro-optimizations.
5. **Re-measure** — before vs after, % delta, confidence. Prove it.
6. **Guard** — add a benchmark or budget test so the win can't silently regress.

## Backend
- CPU/alloc profiling; reduce allocations and copies on hot paths.
- **Database**: read the query plan; add/adjust indexes; kill N+1s (batch/join); paginate;
  connection pooling; avoid SELECT *; cache read-heavy, low-churn data.
- Concurrency: parallelize independent I/O; avoid blocking the event loop; bound queues.
- Serialization and I/O are frequent hidden costs; stream instead of buffering large payloads.

## Frontend (Core Web Vitals)
- **LCP**: prioritize/preload the hero; cut render-blocking CSS/JS; server-render above the fold.
- **INP**: keep the main thread free; break up long tasks; debounce; offload to workers.
- **CLS**: reserve space for media/embeds; avoid inserting content above existing content.
- Ship less JS: code-split, tree-shake, lazy-load. Right-size images (modern formats, srcset).

## Load testing
- k6 / Artillery / Locust with realistic traffic shapes (think time, ramp, peaks). Measure p50/
  p95/p99 latency and error rate, not just averages. Find the knee before it finds you.

## Benchmarking discipline
- Micro-benchmarks (hyperfine, criterion, BenchmarkDotNet) need warm-up and enough iterations.
- Beware measuring the benchmark harness, JIT warm-up, or a cold cache. Control for noise.

## Handoffs
- Architectural performance (caching layer, CDN, read replicas) → `alfred-architect`.
- A "slow" that is actually a correctness bug → `alfred-debugger`. Infra sizing → `alfred-cloud`.

## Anti-patterns
- Optimizing without profiling. Micro-tuning cold paths. Trading correctness/readability for
  unmeasured speed. Reporting "faster" with no numbers.
