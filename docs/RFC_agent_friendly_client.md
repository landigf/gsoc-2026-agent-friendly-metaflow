# RFC: Agent-Friendly Metaflow Client

**Status:** Proposal (GSoC 2026)
**Author:** landigf
**Date:** 2026-03-17

## Motivation

Metaflow makes it easy for humans to inspect runs, tasks, and artifacts through the Python Client API. You write `Flow("MyFlow").latest_run` and get back a rich object you can poke at interactively. This works well in notebooks and scripts where a human is in the loop.

AI agents operate differently. An agent debugging a failed pipeline doesn't browse objects one at a time. It asks a question: "which tasks failed in the latest run?" and needs a direct answer. With the current Client API, answering that question requires fetching every task, checking each one's `_success` artifact from the datastore, and filtering client-side. For a 50-task foreach, that's 56 HTTP calls and about 1.7 seconds.

The Metaflow UI already solved this problem. The UI Backend Service queries the same Postgres database and returns task status as a field in the response. Two HTTP calls, 38 milliseconds, same answer.

The Python Client API just can't use it.

## The Problem, Quantified

When an agent calls `task.successful` on a `Task` object, here is what happens:

```python
# client/core.py, Task class
@property
def successful(self) -> bool:
    try:
        return self["_success"].data  # fetches artifact, unpickles a boolean
    except KeyError:
        return False
```

Each call to `.data` triggers:
1. `GET /flows/{flow}/runs/{run}/steps/{step}/tasks/{task}/artifacts/_success` (metadata service)
2. Read the artifact blob from the datastore (S3 or local filesystem)
3. Unpickle a Python boolean

For a foreach with 50 parallel tasks, finding the one that failed costs:

| Metric | Client API (today) | UI Backend (same DB) |
|--------|-------------------|---------------------|
| HTTP calls | 56 | 2 |
| Datastore reads | 50 | 0 |
| Wall-clock time | ~1,700ms | ~38ms |
| Data transferred | ~50 pickled booleans + metadata | 1 JSON response |

The UI Backend computes status via a SQL JOIN on the `metadata_v3` table, looking for the `attempt_ok` field that the Metaflow runtime writes when a task finishes. No artifact fetching, no unpickling, no per-task round-trips.

But the UI Backend is not the only option. The metadata service itself has `filter_tasks_by_metadata` (since v2.5.0) that can query `attempt_ok` in a single call. Three-path benchmark on a 50-task foreach with 1 failure:

| Path | HTTP calls | Time | Infrastructure required |
|------|-----------|------|------------------------|
| A: Naive Client API | 56 | 3,803ms | Metadata service (always available) |
| C: Smart Metadata | 4 | 489ms | Metadata service (always available) |
| B: UI Backend | 2 | 23ms | Metadata service + UI Backend (optional) |

All three paths return the same result. Path C is the practical target: 7.8x faster than today, no extra infrastructure.

## Key Discovery: The Infrastructure Already Exists

Before proposing new architecture, we discovered three things that change the approach entirely:

**1. `filter_tasks_by_metadata` already works for finding failures.** Since metadata service v2.5.0, calling `filter_tasks_by_metadata(flow, run, step, "attempt_ok", "False")` returns all failed task IDs in a single HTTP call. No artifact fetching. No unpickling. This endpoint exists today but is not used for failure detection.

**2. The metadata service DB layer already supports LIMIT/ORDER BY.** The `find_records()` function in `postgres_async_db.py` accepts `limit`, `offset`, and `order` parameters. The HTTP endpoints just don't expose them. Adding `?_limit=10&_order=-ts_epoch` is ~15 lines per endpoint.

**3. Task status lives in `metadata_v3`, not just in artifacts.** When a task finishes, the runtime writes `attempt_ok = "True"` or `"False"` to the metadata table. The UI Backend reads this via SQL. The metadata service has the same data but doesn't expose it as a queryable field.

These discoveries mean we don't need the UI Backend as a dependency. The metadata service — which every deployment has — can answer agent queries efficiently with small, targeted improvements.

## Proposed Architecture

Three layers, each independently useful, each building on the one below. No dependency on the UI Backend service.

### Layer 1: Agent Query Utilities (extension package, no core changes)

A pip-installable `metaflow-agent-client` extension package that uses existing but underutilized metadata service capabilities:

```python
from metaflow_extensions.agent_client import find_failures, get_recent_runs

# 4 HTTP calls instead of 56, using filter_tasks_by_metadata
failures = find_failures("MyFlow/42")

# Bounded iteration instead of fetching all runs
recent = get_recent_runs("MyFlow", limit=5)
```

Internally, `find_failures` calls `filter_tasks_by_metadata(flow, run, step, "attempt_ok", "False")` for each step. This already works on any deployment with metadata service >= 2.5.0. No UI Backend needed.

When the UI Backend IS available (configured via `METAFLOW_UI_BACKEND_URL`), functions opportunistically use it for a further speedup (2 calls vs 4). But this is a bonus, not a requirement.

### Layer 2: Metadata Service Endpoint Improvements (~150 lines)

Expose capabilities the DB layer already has but the HTTP API doesn't:

| Change | Lines | What it enables |
|--------|-------|-----------------|
| Add `_limit` to listing endpoints | ~15 | "Give me the last 5 runs" without fetching all history |
| Add `_order` to listing endpoints | ~15 | Sort by timestamp, run number |
| Simplified status endpoint via `attempt_ok` | ~40 | Task status without artifact fetching |
| Better `attempt_ok` filtering shorthand | ~25 | `?status=failed` instead of regex |

These are small changes to `metadata_service/api/task.py` and `run.py`. They benefit every Metaflow user, not just agents. The underlying `find_records()` function already accepts these parameters — we're just wiring them to HTTP.

### Layer 3: ServiceMetadataProvider Enhancement

Make the existing Python Client API automatically use the new query capabilities:

```python
# Existing version-gating pattern in service.py:260
if cls._supports_query_params is None:
    version = cls._version(None)
    cls._supports_query_params = (
        version is not None
        and version_parse(version) >= version_parse("X.Y.Z")
    )
```

When True, listing operations append `?_limit=N&_order=-ts_epoch` to the URL. When False (old service), behavior is identical to today. Existing code gets faster transparently.

## API Surface

### Agent Utility Functions

```python
def get_recent_runs(flow_id: str, limit: int = 10, status: str = None) -> list[dict]:
    """
    List recent runs for a flow.

    Returns at most `limit` runs, ordered by recency. Optionally filter
    by status ("completed", "failed", "running").

    Makes 1 HTTP call when UI Backend is available.
    Falls back to Flow(flow_id) iteration otherwise.

    Parameters
    ----------
    flow_id : str
        Name of the flow (e.g., "MyFlow")
    limit : int, default 10
        Maximum number of runs to return
    status : str, optional
        Filter by run status

    Returns
    -------
    list of dict
        Each dict contains: run_number, status, ts_epoch, duration, user
    """

def find_failures(run_pathspec: str, max_tasks: int = 100) -> dict:
    """
    Find all failed tasks in a run.

    Checks every step in the run and returns tasks whose status is "failed".
    This is the agent-friendly equivalent of iterating all tasks and
    checking task.successful on each one.

    Makes 2-4 HTTP calls when UI Backend is available.
    Falls back to O(tasks) calls otherwise.

    Parameters
    ----------
    run_pathspec : str
        Run identifier (e.g., "MyFlow/42")
    max_tasks : int, default 100
        Maximum tasks to inspect per step (pagination limit)

    Returns
    -------
    dict
        Keys: failures (list of pathspecs), total_tasks (int),
        steps_checked (int), elapsed_ms (float)
    """

def batch_run_status(flow_id: str, limit: int = 10) -> list[dict]:
    """
    Get status of recent runs in a single call.

    Parameters
    ----------
    flow_id : str
        Name of the flow
    limit : int, default 10
        Maximum number of runs

    Returns
    -------
    list of dict
        Each dict contains: run_number, status, duration, finished_at
    """

def run_summary(run_pathspec: str) -> dict:
    """
    Structured overview of a run.

    Returns step names, task counts, status breakdown, and timing.
    Useful for agents that need to understand what a run did before
    drilling into specific tasks.

    Parameters
    ----------
    run_pathspec : str
        Run identifier (e.g., "MyFlow/42")

    Returns
    -------
    dict
        Keys: flow_id, run_number, status, steps (list of step summaries),
        total_tasks, failed_tasks, duration_ms
    """

def get_runs_since(flow_id: str, hours: int = 24) -> list[dict]:
    """
    List runs started within the last N hours.

    Makes 1 HTTP call when UI Backend is available.
    Falls back to iterating all runs and filtering by timestamp otherwise.

    Parameters
    ----------
    flow_id : str
        Name of the flow
    hours : int, default 24
        Time window in hours

    Returns
    -------
    list of dict
        Same format as get_recent_runs
    """

def tail_logs(task_pathspec: str, stream: str = "stdout",
              n_lines: int = 50) -> str:
    """
    Retrieve the last N lines of a task's stdout or stderr.

    Parameters
    ----------
    task_pathspec : str
        Task identifier (e.g., "MyFlow/42/train/7")
    stream : str, default "stdout"
        "stdout" or "stderr"
    n_lines : int, default 50
        Number of lines to return

    Returns
    -------
    str
        Log content, or empty string if logs are unavailable
    """
```

### Configuration

Two new configuration values, both optional:

```python
# In metaflow_config.py
UI_BACKEND_URL = from_conf("UI_BACKEND_URL")  # e.g., "http://localhost:8083"
```

When `METAFLOW_UI_BACKEND_URL` is set, agent utility functions use the fast path. When it's not set, they fall back to the standard Client API. No other configuration changes are needed.

## Backward Compatibility

This proposal is designed to be fully backward-compatible.

**No existing behavior changes.** `Flow()`, `Run()`, `Step()`, `Task()` work identically. The `__iter__` method on `MetaflowObject` is not modified. `task.successful` still works the same way. Nothing breaks.

**Graceful degradation.** When the metadata service is an older version that doesn't support `_limit` or `_order`, the provider silently falls back to the current behavior (fetch all, filter client-side). The version check follows the exact pattern already used for `_supports_attempt_gets` and `_supports_tag_mutation`.

**UI Backend is optional.** If `METAFLOW_UI_BACKEND_URL` is not configured, agent utility functions use the fallback path. They're slower (more HTTP calls) but correct. No hard dependency on the UI Backend.

**No new required configuration.** All new config values have sensible defaults: `UI_BACKEND_URL` defaults to `None`, which means "use fallback path." Existing deployments are unaffected.

**Old client + new service:** Old clients don't pass query params. The service ignores missing params and returns all results (current behavior). Safe.

**New client + old service:** The version check detects the old service and skips query params. Falls back to current behavior. Safe.

## What Changes vs What Stays the Same

### Changes

- New `_limit` and `_order` query parameter support in metadata service listing endpoints
- New `_supports_query_params` version flag in `ServiceMetadataProvider`
- New `METAFLOW_UI_BACKEND_URL` configuration entry
- New agent utility module with the functions described above
- New tests

### Stays the same

- `MetaflowObject.__iter__`, `__getitem__`, `__contains__` -- unchanged
- `MetadataProvider` base class interface -- unchanged (new method is additive)
- `_get_object_internal` method signature -- unchanged
- All existing metadata providers (local, service, spin) -- unchanged behavior
- Client API classes (Flow, Run, Step, Task, DataArtifact) -- unchanged
- Plugin registration mechanism -- unchanged
- Datastore operations -- unchanged

## Drawbacks and Alternatives Considered

### Why not just use the UI Backend directly?

The UI Backend was built for the web UI, not for programmatic access. It has no stability guarantees for its API, its response format differs from the metadata service (paginated with `data`, `links`, `pages` wrapper), and it requires a separate deployment. Making the Client API depend on it directly would couple two services that are currently independent.

The agent utility functions use the UI Backend opportunistically (when configured) but never require it. This keeps the dependency optional and the fallback path always available.

### Why not a new AgentMetadataProvider type?

A metadata provider represents a storage backend (local files, a service, etc.), not a query style. Creating an `AgentMetadataProvider` that talks to the same service but with different query parameters would be architecturally confusing. It would also force users to choose between "service" and "agent" at configuration time, which is the wrong abstraction. The right fix is making the existing "service" provider smarter when the service supports it.

### Why not add status filtering to the metadata service?

The task status computation in the UI Backend is a 100+ line SQL CASE statement with lateral JOINs across the metadata table. It handles heartbeat timeouts, retry attempts, old runs without heartbeats, and several edge cases. Porting this into the metadata service is worthwhile but complex, and should be discussed with maintainers before attempting it.

The phased approach starts with `_limit` and `_order` (straightforward SQL clauses), then adds status as a follow-up if the approach is validated.

## Testing Strategy

1. **Unit tests:** Mock HTTP responses for both metadata service and UI Backend. Verify correct URL construction, parameter passing, and fallback behavior.

2. **Integration tests:** Run against the local dev stack (Postgres + metadata service + UI Backend). Verify that agent utility functions return the same results through both paths.

3. **Backward compatibility matrix:**
   - {old service, new service} x {with UI Backend, without UI Backend}
   - All combinations must produce correct results.

4. **Performance benchmark:** Automated script that measures call counts and latency for the standard agent scenarios, comparing old path vs new path.

5. **Agent simulation:** Scripted scenarios where an agent uses the utility functions to debug flows, measuring the improvement in calls and latency compared to naive Client API usage.
