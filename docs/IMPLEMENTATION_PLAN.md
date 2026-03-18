# Implementation Plan: Agent-Friendly Metaflow Client

**Status:** Proposal (GSoC 2026)
**Author:** landigf
**Date:** 2026-03-17

## Where We Are Today

We have a working prototype that proves the concept. Raw `requests.get()` calls to the UI Backend Service answer agent queries in 2 HTTP calls instead of 56. The results are identical to the Client API path, both query the same Postgres database, and the speedup is 17-45x depending on measurement context.

But the prototype is not integrated into Metaflow. It lives outside the framework, uses hardcoded URLs, has no fallback logic, no tests, no configuration support, and no documentation. This document describes what it takes to make it real.

## Why We Can't Ship It Today

Four constraints prevent going from prototype to production right now:

**1. Core Runtime gate.** Changes to `metadata_provider/` and `plugins/metadata_providers/` are classified as Core Runtime in CONTRIBUTING.md. They require a pre-approved issue, a maintainer-aligned approach, a minimal reproduction, and a technical rationale. We need mentor buy-in before touching these files.

**2. Two-repo coordination.** The metadata service lives in `Netflix/metaflow-service`. The Python client lives in `Netflix/metaflow`. Adding query parameters requires changes in both repos with coordinated versioning. The client needs to detect what the service supports.

**3. GSoC policy.** AGENTS_EXTERNAL.md explicitly states: "Submitting unsolicited PRs is NOT part of the GSoC application process and these PRs will be closed without review." Code contributions happen after mentor acknowledgement, during the GSoC coding period.

**4. Status computation complexity.** The UI Backend derives task status through a 100+ line SQL CASE statement with lateral JOINs on `metadata_v3` for `attempt_ok`, heartbeat timeout detection, retry attempt tracking, and old-run failure cutoffs (see `ui_backend_service/data/db/tables/task.py:171-202`). Porting this to the simpler metadata service needs careful discussion.

## Approach: Layered, from Lowest Risk to Highest Impact

### Phase 1: Extension Package (no core changes)

**What:** Build `metaflow-agent-client` as a standalone extension package using Metaflow's `metaflow_extensions` mechanism.

**Why start here:**
- Zero risk to existing users
- No core changes, no mentor gate needed to start building
- Extension packages are the intended mechanism for adding functionality
- Provides a usable tool immediately, even if core integration comes later

**How:**

```
metaflow_extensions/
    agent_client/
        __init__.py            # Extension registration
        plugins/
            __init__.py        # Empty (no new decorators/providers yet)
        agent_query.py         # Public API: find_failures(), get_recent_runs(), etc.
        _ui_backend_client.py  # HTTP client for UI Backend with connection pooling
        _fallback.py           # Fallback implementations using standard Client API
        _config.py             # Configuration: UI_BACKEND_URL detection
```

Each agent utility function follows the same pattern:

```python
def find_failures(run_pathspec, max_tasks=100):
    """Find all failed tasks in a run."""
    ui_backend = _get_ui_backend_client()
    if ui_backend is not None:
        return _find_failures_fast(ui_backend, run_pathspec, max_tasks)
    return _find_failures_fallback(run_pathspec, max_tasks)
```

The fast path uses the UI Backend. The fallback path uses the existing Client API. Both return the same data structure. Tests verify they produce identical results.

**Configuration:**

```bash
# Optional. When set, agent utilities use the fast path.
export METAFLOW_UI_BACKEND_URL=http://localhost:8083

# No config needed for fallback path. It always works.
```

**Testing:**

```bash
# Unit tests (mocked HTTP)
cd metaflow-agent-client && python -m pytest tests/unit/ -v

# Integration tests (requires local dev stack)
python -m pytest tests/integration/ -v

# Benchmark (compare fast path vs fallback)
python benchmarks/compare_paths.py
```

### Phase 2: Metadata Service Query Parameters

**What:** Add `_limit` and `_order` query parameters to the metadata service's listing endpoints.

**Files to modify:**
- `metaflow-service/services/metadata_service/api/run.py` -- parse `_limit`, `_order` from request.query
- `metaflow-service/services/metadata_service/api/task.py` -- same
- `metaflow-service/services/data/postgres_async_db.py` -- add `LIMIT` and `ORDER BY` to SQL

**What the change looks like:**

The metadata service's `get_runs` currently does:
```sql
SELECT * FROM runs_v3 WHERE flow_id = $1
```

After the change:
```sql
SELECT * FROM runs_v3 WHERE flow_id = $1 ORDER BY ts_epoch DESC LIMIT 10
```

The `_limit` and `_order` parameters are optional. When absent, the query returns all results (current behavior). This is how the UI Backend's `find_records` pattern works; we follow the same approach.

**Backward compatibility:**
- Old clients never send `_limit` or `_order`. The service returns all results. No change.
- New clients send `_limit=10&_order=-ts_epoch`. The service returns a bounded, ordered result set.
- Bumps the metadata service version. New clients detect the version and use the params.

**Testing:**
```bash
# Service tests
cd metaflow-service && python -m pytest tests/ -v

# Manual verification
curl "http://localhost:8080/flows/MyFlow/runs?_limit=3&_order=-ts_epoch"
```

### Phase 3: ServiceMetadataProvider Enhancement

**What:** Make `ServiceMetadataProvider._get_object_internal()` pass `_limit` and `_order` when the service supports them.

**File:** `metaflow/plugins/metadata_providers/service.py`

**The change (following the existing version-gating pattern):**

```python
# Add alongside _supports_attempt_gets and _supports_tag_mutation
_supports_query_params = None

@classmethod
def _get_object_internal(cls, obj_type, obj_order, sub_type, sub_order,
                         filters, attempt, *args):
    # ... existing attempt check ...

    if sub_type == "self":
        # ... existing single-object fetch (unchanged) ...
        pass

    # For listing operations, use query params when supported
    if obj_type != "root":
        url = ServiceMetadataProvider._obj_path(*args[:obj_order])
    else:
        url = ""

    if sub_type == "metadata":
        url += "/metadata"
    elif sub_type == "artifact" and obj_type == "task" and attempt is not None:
        url += "/attempt/%s/artifacts" % attempt
    else:
        url += "/%ss" % sub_type

    # NEW: append query params when service supports them
    if cls._supports_query_params is None:
        version = cls._version(None)
        cls._supports_query_params = (
            version is not None
            and version_parse(version) >= version_parse("X.Y.Z")
        )

    # Only add query params for child listings, not metadata or artifacts
    query_params = {}
    if cls._supports_query_params and sub_type not in ("metadata", "artifact"):
        # Default: order by timestamp descending, limit to 1000
        query_params["_order"] = "-ts_epoch"
        query_params["_limit"] = "1000"

    try:
        v, _ = cls._request(None, url, "GET", query_params=query_params)
        # ... rest unchanged ...
```

**Backward compatibility:**
- When `_supports_query_params` is False (old service), no params are sent. Behavior is identical to today.
- The default `_limit=1000` prevents unbounded fetches but is high enough that most flows won't hit it. Users with more than 1000 tasks in a single step would need to use the agent utilities for full iteration.

**Testing:**
```bash
cd metaflow/test/unit && python -m pytest -v -k "test_service_metadata"
```

### Phase 4: Agent Simulation and Documentation

**What:** Validate the implementation with a realistic agent workflow and write documentation for both humans and agents.

**Agent simulation test:**

```python
"""
Simulate an agent debugging a failed pipeline.
Measure HTTP calls and latency for old path vs new path.
"""

SCENARIOS = [
    {
        "name": "Find failed runs",
        "question": "Which of my last 10 ForeachFlow runs failed?",
        "old_path": lambda: [r for r in Flow("ForeachFlow")
                             if not r.successful][:10],
        "new_path": lambda: get_recent_runs("ForeachFlow", limit=10,
                                            status="failed"),
    },
    {
        "name": "Find failed tasks",
        "question": "What tasks failed in ForeachFlow/9?",
        "old_path": lambda: [t.pathspec for s in Run("ForeachFlow/9")
                             for t in s if not t.successful],
        "new_path": lambda: find_failures("ForeachFlow/9")["failures"],
    },
    {
        "name": "Recent activity",
        "question": "Show me runs from the last 24 hours",
        "old_path": lambda: [r for r in Flow("ForeachFlow")
                             if r.created_at > yesterday],
        "new_path": lambda: get_runs_since("ForeachFlow", hours=24),
    },
]
```

Each scenario records call counts (via monkeypatched `_request`), wall-clock time, and bytes transferred. The output is a comparison table.

**Documentation structure:**

| Document | Audience | Format |
|----------|----------|--------|
| "Using Metaflow with AI Agents" | Human developers | Narrative guide (.md) |
| Agent API Reference | LLM agents | Structured JSON schema |
| Inline docstrings | Both | NumPy-style in source code |
| Pattern cookbook | Both | Before/after examples |

## Drawbacks to Consider

**1. Extension package has limits.** An extension package can add new functions but cannot transparently improve existing Client API iteration. Users must explicitly call `find_failures()` instead of iterating tasks. The real win comes in Phase 3, when `ServiceMetadataProvider` passes query params automatically.

**2. Two-path maintenance burden.** Every agent utility function has a fast path and a fallback path. Both must produce identical results. This doubles the test surface. Mitigation: automated comparison tests that run both paths on the same data and assert equality.

**3. UI Backend dependency for fast path.** The fast path requires the UI Backend to be deployed and configured. In environments where only the metadata service is available (common in early-stage setups), agents get the fallback path. This is correct but slower.

**4. Version coordination.** Phase 3 requires the metadata service version to be bumped and the client to detect it. If a user upgrades the client but not the service (or vice versa), the version check ensures correct fallback. But the user doesn't get the speed improvement until both are upgraded.

**5. `_limit` default may surprise.** Adding a default `_limit=1000` to listings means that a flow with 1001+ tasks in a single step would silently return only the first 1000. This is a trade-off between safety (no unbounded fetches) and completeness. The limit should be configurable and documented.

## Verification Checklist

Before each phase is considered complete:

- [ ] All existing tests pass (`cd test/unit && python -m pytest -v`)
- [ ] New tests cover the new functionality
- [ ] Backward compatibility matrix tested: {old service, new service} x {with UI Backend, without}
- [ ] Benchmark shows expected improvement (call count and latency)
- [ ] `pre-commit` / `black` formatting applied
- [ ] Docstrings follow NumPy/SciPy style
- [ ] No new required configuration (all new config has sensible defaults)
- [ ] Agent utility functions return identical results through fast and fallback paths
