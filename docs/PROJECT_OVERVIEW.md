# Project Overview: What We Found, What We're Building, How It All Fits Together

## The Setup (What Exists Today)

Metaflow stores everything about your flows in a Postgres database: runs, steps, tasks, artifacts, metadata. Two web services sit on top of that database:

```
┌─────────────────────────────────────────────────────┐
│                   PostgreSQL                         │
│  (flows, runs, steps, tasks, artifacts, metadata)    │
└──────────────┬──────────────────────┬───────────────┘
               │                      │
    ┌──────────┴──────────┐  ┌───────┴────────────┐
    │  Metadata Service   │  │  UI Backend Service │
    │  (port 8080)        │  │  (port 8083)        │
    │                     │  │                     │
    │  Every deployment   │  │  Optional, only if  │
    │  has this           │  │  you run the Web UI │
    └──────────┬──────────┘  └───────┬────────────┘
               │                      │
    ┌──────────┴──────────┐  ┌───────┴────────────┐
    │  Python Client API  │  │  Web UI (browser)   │
    │  What agents use    │  │                     │
    └─────────────────────┘  └────────────────────┘
```

The **Metadata Service** is the core. Every Metaflow deployment has it. The Python Client API talks exclusively to this service.

The **UI Backend** is optional. It powers the web dashboard. Many deployments don't have it at all.

## The Problem

When an AI agent asks "which tasks failed?", the Client API does this:

```python
for task in step:
    if not task.successful:  # ← THIS is the problem
        print(task.pathspec)
```

Inside `task.successful`:
1. HTTP call to fetch the `_success` artifact metadata
2. Read the actual boolean from S3/local datastore
3. Unpickle the Python boolean
4. Return True or False

**For each task.** 50 tasks = 50 HTTP calls + 50 datastore reads + 50 unpickle operations. Just to get 50 booleans.

Total for a 50-task foreach: **56 HTTP calls, ~3800ms**.

## The Three Discoveries

### Discovery 1: The metadata service already has the data

When a task finishes, the Metaflow runtime writes `attempt_ok = "True"` or `"False"` to the `metadata_v3` table. This is how the UI Backend computes status — it reads `attempt_ok` with a SQL JOIN, not by fetching artifacts.

The metadata service has this data. It just doesn't expose it conveniently.

### Discovery 2: filter_tasks_by_metadata already works

Since metadata service v2.5.0, there's an endpoint nobody uses for this purpose:

```
GET /flows/{flow}/runs/{run}/steps/{step}/filtered_tasks?metadata_field_name=attempt_ok&pattern=False
```

This returns all failed task IDs **in a single HTTP call**. No artifact fetching. No unpickling. The `ServiceMetadataProvider` already has a method for it:

```python
ServiceMetadataProvider.filter_tasks_by_metadata(
    "ForeachFlow", "8", "process", "attempt_ok", "False"
)
# → ["ForeachFlow/8/process/126"]    ← one call, done
```

### Discovery 3: The DB layer supports pagination, the API just doesn't expose it

The metadata service's database layer (`postgres_async_db.py`) already has `find_records()` with `limit`, `offset`, and `order` parameters. The HTTP endpoints just don't pass them through. Adding `?_limit=10&_order=-ts_epoch` is ~15 lines per endpoint.

## The Three-Path Benchmark

We built a Metaflow flow that benchmarks all three approaches as parallel branches:

```
         start
        /  |  \
path_a  path_b  path_c
  naive   ui     smart
        \  |  /
        compare
          |
         end
```

Results (all three find the same failed task):

```
Path                       Calls   Time     What it does
────────────────────────── ────── ───────── ─────────────────────────────────
A: Naive Client API           56   3803ms   Iterate all tasks, fetch _success per task
C: Smart Metadata              4    489ms   filter_tasks_by_metadata (no UI Backend!)
B: UI Backend                  2     23ms   Requires extra service deployment

C vs A:  52 fewer calls, 7.8x faster, works everywhere
B vs A:  54 fewer calls, 165x faster, but requires UI Backend
```

**Path C is the sweet spot.** It's 7.8x faster than the naive path, uses only the metadata service (which every deployment has), and needs zero infrastructure changes. It's 2 more calls than the UI Backend path, but it has no extra service dependency.

## What We're Actually Building

The GSoC project has three layers, each making things better:

### Layer 1: Agent Query Utilities (extension package)

A pip-installable `metaflow-agent-client` package with smart functions that use `filter_tasks_by_metadata` and other existing but underutilized capabilities:

```python
from metaflow_extensions.agent_client import find_failures, get_recent_runs

failures = find_failures("ForeachFlow/8")
# Uses filter_tasks_by_metadata internally — 4 calls instead of 56
# Works on EVERY deployment with metadata service >= 2.5.0
```

**No new infrastructure. No UI Backend dependency. Just smarter use of what already exists.**

### Layer 2: Metadata Service Improvements (~150 lines)

Expose capabilities the DB layer already has:

| Change | Lines | Impact |
|--------|-------|--------|
| Add `_limit` to listing endpoints | ~15 | "Show me the last 5 runs" without fetching all |
| Add `_order` to listing endpoints | ~15 | Sort by timestamp, run number |
| Add simplified status endpoint | ~40 | Get task status without artifact fetching |
| Better `attempt_ok` filtering | ~25 | More intuitive than regex matching |

These are small, targeted changes to the metadata service. They benefit everyone — not just agents. The DB infrastructure (`find_records` with limit/offset/order) already exists; we're just wiring it to the HTTP layer.

### Layer 3: ServiceMetadataProvider Enhancement

Make the existing Python client automatically use the new capabilities:

```python
# After our changes, this code gets faster automatically:
for task in step:
    if not task.successful:  # now uses metadata instead of artifacts
        print(task.pathspec)
```

Version-gated (same pattern used for `_supports_attempt_gets`): detects if the metadata service is new enough, falls back to current behavior if not.

### Where the UI Backend fits

The UI Backend becomes a **bonus optimization**, not a requirement:

```
             ┌──────────────────────────────┐
             │  find_failures("Flow/42")    │
             └──────────────┬───────────────┘
                            │
              ┌─────────────┼──────────────┐
              │             │              │
         Best case     Good case      Baseline
         UI Backend    Smart Meta     Naive API
         2 calls       4 calls        56 calls
         23ms          489ms          3803ms
              │             │              │
              └─────────────┴──────────────┘
                            │
                     Same result ✓
```

Every deployment gets the "Good case" (4 calls). Deployments with the UI Backend get the "Best case" (2 calls). Nobody is stuck with the "Baseline" (56 calls).

## How We Test

### Local dev stack (Docker/minikube):
- PostgreSQL, Metadata Service (8080), UI Backend (8083), Web UI (3000)

### Test flows:
- **SimpleFlow**: 2 steps, always succeeds (3 runs)
- **MultiStepFlow**: 4 steps, always succeeds (3 runs)
- **ForeachFlow**: 50 parallel tasks, task 47 fails (2 runs)

### Benchmark scripts:
| Script | What it measures |
|--------|-----------------|
| `demo/benchmark_three_paths.py` | 3-way comparison as Metaflow flow (visible in UI DAG) |
| `demo/full_audit.py` | All 6 GSoC use cases benchmarked |
| `demo/demo_two_services.py` | Head-to-head Client API vs UI Backend |

### What we verify:
1. **Correctness**: All paths return identical results
2. **Performance**: HTTP calls, wall-clock time
3. **No dependency**: Smart Metadata path works without UI Backend
4. **Backward compat**: Old client + new service = works. New client + old service = works.

## The File Map

```
/Users/landigf/Desktop/Code/GSoC/
├── metaflow/                          # Metaflow source (Python client)
│   └── metaflow/
│       ├── client/core.py             # Flow, Run, Step, Task classes
│       └── plugins/metadata_providers/
│           └── service.py             # ServiceMetadataProvider
│                                        ↑ filter_tasks_by_metadata lives here
│
├── metaflow-service/                  # Metaflow services (Postgres-backed)
│   └── services/
│       ├── metadata_service/          # Core service (every deployment)
│       ├── ui_backend_service/        # Optional UI service
│       └── data/postgres_async_db.py  # DB layer (already has LIMIT/ORDER!)
│
├── demo/                              # Benchmarks and test flows
│   ├── benchmark_three_paths.py       # 3-way DAG comparison
│   ├── full_audit.py                  # All 6 GSoC use cases
│   ├── foreach_flow.py               # 50-task flow with intentional failure
│   └── ...
│
└── docs/                              # Documentation
    ├── PROJECT_OVERVIEW.md            # ← You are here
    ├── RFC_agent_friendly_client.md   # Formal specification
    ├── IMPLEMENTATION_PLAN.md         # How to make it real
    └── GSOC_TIMELINE.md              # 350-hour timeline
```

## The One-Paragraph Summary

The Metaflow metadata service already stores task success/failure status (`attempt_ok`) and its database layer already supports pagination — neither capability is exposed through the API. Today, finding a failed task costs 56 HTTP calls because the Client API fetches an artifact per task. Using `filter_tasks_by_metadata` (available since v2.5.0), we can answer the same question in 4 calls — 7.8x faster, no extra infrastructure. The GSoC project builds agent utility functions on top of this, adds pagination and ordering to the metadata service endpoints (~150 lines), and enhances the Client API to use these capabilities automatically. The UI Backend becomes a bonus optimization, not a requirement.
