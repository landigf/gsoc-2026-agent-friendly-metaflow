# GSoC 2026 Timeline: Agent-Friendly Metaflow Client

**Contributor:** landigf
**Organization:** Outerbounds / Metaflow
**Total hours:** 350
**Duration:** 16 weeks (May-August 2026)
**Date:** 2026-03-17

## Overview

This project bridges the gap between what Metaflow's UI Backend can do (rich queries, pagination, status filtering) and what the Python Client API exposes (iterate everything, fetch artifacts one by one). The result is an agent-friendly query layer that reduces HTTP calls by 96% for common agent operations, with full backward compatibility and graceful fallback.

## Pre-GSoC (Now through May)

**Already completed:**
- Audited the full Client API call chain (`get_object` counts, DB query tracing, datastore reads)
- Stood up the local dev stack (Postgres + metadata service + UI Backend)
- Built working prototype with quantitative benchmarks (56 vs 2 calls, 45x speedup)
- Created BenchmarkFlow that visualizes both paths in the Metaflow UI
- Mapped all UI Backend endpoints and confirmed query capabilities
- Wrote RFC specification and implementation plan

**Before coding period starts:**
- Discuss approach with mentor on community Slack
- Get feedback on RFC and implementation plan
- Identify specific issues to link PRs to
- Familiarize with `metaflow_extensions` package structure

---

## Phase 1: Foundation (Weeks 1-4, ~90 hours)

### Week 1: Extension Package Scaffold (22h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Set up `metaflow-agent-client` as a `metaflow_extensions` package | 6h | Working package with `pip install -e .` |
| Port prototype from `metaflow_agent/__init__.py` into extension structure | 6h | `agent_query.py` with all function stubs |
| Implement `_ui_backend_client.py` (HTTP client with `requests.Session`) | 6h | Connection-pooled client with ping/version detection |
| Write unit tests with mocked HTTP responses | 4h | `tests/unit/test_ui_backend_client.py` |

**Milestone:** `pip install metaflow-agent-client && python -c "from metaflow_extensions.agent_client import agent_query"` works.

### Week 2: Fallback Logic and Configuration (22h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Implement `_fallback.py` (all functions via standard Client API) | 8h | Complete fallback implementations |
| Implement `_config.py` (detect `METAFLOW_UI_BACKEND_URL`, probe availability) | 4h | Auto-detection with caching |
| Wire fast path / fallback path routing in each function | 6h | `agent_query.py` with dual paths |
| Unit tests for fallback path | 4h | `tests/unit/test_fallback.py` |

**Milestone:** Every function works with `METAFLOW_UI_BACKEND_URL` set (fast) or unset (fallback).

### Week 3: Core Agent Functions (22h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Implement `get_recent_runs()` (fast + fallback) | 4h | Tested, both paths verified identical |
| Implement `find_failures()` (fast + fallback) | 6h | Tested, handles foreach fans |
| Implement `batch_run_status()` (fast + fallback) | 4h | Tested |
| Integration tests against local dev stack | 6h | `tests/integration/test_agent_query.py` |
| Automated path comparison (assert fast == fallback) | 2h | `tests/integration/test_path_equivalence.py` |

**Milestone:** Three core functions pass all tests against the live dev stack.

### Week 4: Remaining Functions and Benchmark Harness (24h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Implement `run_summary()` | 4h | Tested |
| Implement `tail_logs()` | 4h | Tested, handles missing logs gracefully |
| Implement `get_runs_since()` | 4h | Tested, time-range filtering |
| Build automated benchmark harness | 6h | `benchmarks/compare_paths.py` |
| Run benchmarks, produce comparison table | 4h | `benchmarks/results_phase1.md` |
| Mentor check-in: present Phase 1 results | 2h | Discussion notes |

**Milestone:** Complete extension package with 6 functions, all tested, benchmark showing improvement.

**Phase 1 deliverables:**
- `metaflow-agent-client` extension package (installable)
- 6 agent utility functions with fast path + fallback
- Unit tests, integration tests, path equivalence tests
- Benchmark results

---

## Phase 2: Core Implementation (Weeks 5-8, ~90 hours)

### Week 5: Metadata Service Query Parameters (22h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Study UI Backend's `find_records` pattern in detail | 3h | Understanding of SQL generation |
| Add `_limit` and `_order` to `/flows/{id}/runs` endpoint | 6h | Working endpoint with tests |
| Add `_limit` and `_order` to `/runs/{id}/steps/{name}/tasks` endpoint | 6h | Working endpoint with tests |
| Write metadata service tests | 5h | `tests/test_query_params.py` |
| Manual verification with curl | 2h | Documented curl commands + results |

**Milestone:** `curl "http://localhost:8080/flows/MyFlow/runs?_limit=3&_order=-ts_epoch"` returns 3 runs.

### Week 6: ServiceMetadataProvider Enhancement (22h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Add `_supports_query_params` version flag | 4h | Version detection logic |
| Modify `_get_object_internal` to pass params for listings | 8h | Query param passing for run/task listings |
| Unit tests mocking different service versions | 6h | `test/unit/test_service_provider_query.py` |
| Test old client + new service compatibility | 2h | Verified no regression |
| Test new client + old service fallback | 2h | Verified graceful degradation |

**Milestone:** `Flow("MyFlow").runs()` automatically uses `?_limit=1000&_order=-ts_epoch` when service supports it.

### Week 7: Status Query Evaluation (22h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Analyze UI Backend's status SQL CASE logic in detail | 6h | Documented logic with decision tree |
| Prototype simplified status query using `attempt_ok` metadata | 8h | Working prototype or documented infeasibility |
| If feasible: implement + test in metadata service | 6h | Status endpoint or documented rationale for deferral |
| If not feasible: document as future work, rely on UI Backend path | 2h | `docs/STATUS_COMPUTATION_ANALYSIS.md` |

**Milestone:** Clear decision on status filtering approach with documented rationale.

### Week 8: Integration Testing (24h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| End-to-end test: extension package -> metadata service with query params | 6h | Passing integration tests |
| Backward compatibility matrix testing | 8h | 4 combinations tested and documented |
| Performance regression check (verify common path not slower) | 4h | Benchmark results |
| Update extension package to use metadata service params when available | 4h | Three-tier fast path: UI Backend > query params > fallback |
| Mentor check-in: present Phase 2 results | 2h | Discussion notes |

**Milestone:** Full integration across all layers, backward compatibility verified.

**Phase 2 deliverables:**
- Metadata service with `_limit` and `_order` support
- Enhanced `ServiceMetadataProvider` with version-gated query params
- Backward compatibility matrix (4 combinations tested)
- Status computation analysis document

---

## Phase 3: Agent Integration and Testing (Weeks 9-12, ~90 hours)

### Week 9: Agent Simulation Framework (22h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Design 5 agent debugging scenarios with expected outcomes | 4h | `tests/scenarios/` with scenario definitions |
| Build instrumented test harness (count calls, measure latency, track bytes) | 8h | `benchmarks/agent_simulation.py` |
| Run scenarios with old path (Client API) and record baselines | 4h | Baseline measurements |
| Run scenarios with new path (agent utilities) and record results | 4h | Improvement measurements |
| Generate comparison report | 2h | `benchmarks/results_agent_simulation.md` |

**Agent scenarios:**

| # | Question | Old path calls | New path calls | Expected speedup |
|---|----------|---------------|----------------|-----------------|
| 1 | "Which of my last 10 runs failed?" | ~20+ | 1-2 | 10-20x |
| 2 | "What tasks in run X failed?" | 56 | 2-4 | 15-30x |
| 3 | "Show me the logs from the failed task" | 58+ | 3-5 | 12-20x |
| 4 | "Runs from the last 24 hours" | all runs | 1 | proportional to run count |
| 5 | "Summary of run X" | ~60+ | 2-3 | 20-30x |

### Week 10: Tool-Calling Integration (22h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Research MCP (Model Context Protocol) tool definitions | 4h | Decision on MCP vs JSON schema |
| Create machine-readable function schemas | 6h | `schemas/agent_tools.json` |
| Write system prompt fragment for agents | 4h | `docs/AGENT_SYSTEM_PROMPT.md` |
| Test with Claude: give it only the schema + docs, ask it to debug a flow | 6h | Transcript showing effective usage |
| Iterate on schema/prompt based on Claude's behavior | 2h | Refined schema |

**Tool schema format:**

```json
{
  "tools": [
    {
      "name": "find_failures",
      "description": "Find all failed tasks in a Metaflow run. Use this instead of iterating tasks manually.",
      "input_schema": {
        "type": "object",
        "properties": {
          "run_pathspec": {"type": "string", "description": "e.g. 'MyFlow/42'"},
          "max_tasks": {"type": "integer", "default": 100}
        },
        "required": ["run_pathspec"]
      },
      "cost_hint": "2-4 HTTP calls (fast path) or O(tasks) calls (fallback)",
      "when_to_use": "When the user asks about failures, errors, or broken tasks in a run"
    }
  ]
}
```

### Week 11: Robustness and Edge Cases (22h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Test with large foreach fans (1000+ tasks) | 4h | Pagination correctness verified |
| Network failure handling (UI Backend down, metadata service timeout) | 6h | Graceful degradation tested |
| Partial availability (UI Backend down, metadata service up) | 4h | Fallback path activates correctly |
| Concurrent access testing | 4h | Thread safety verified |
| Error message quality audit | 2h | Informative warnings on fallback |
| Edge case: empty flows, no runs, no tasks, no failures | 2h | Correct empty results |

### Week 12: Performance Validation (24h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Generate realistic dataset (100+ runs, 10+ flows, 1000+ tasks) | 6h | Test data generation script |
| Run full benchmark suite on realistic data | 6h | `benchmarks/results_full.md` |
| Profile fallback path (ensure not slower than baseline) | 4h | Profile results |
| Identify remaining bottlenecks, optimize if straightforward | 4h | Optimization notes |
| Create visual comparison (charts/tables for proposal) | 2h | Figures for documentation |
| Mentor check-in: present Phase 3 results | 2h | Discussion notes |

**Phase 3 deliverables:**
- Agent simulation framework with 5 scenarios
- Machine-readable tool schemas (JSON)
- System prompt fragment for LLM agents
- Claude integration test transcript
- Robustness test suite
- Performance report on realistic data

---

## Phase 4: Documentation and Polish (Weeks 13-16, ~80 hours)

### Week 13: User Documentation (20h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Write "Using Metaflow with AI Agents" guide | 8h | `docs/guides/agent_guide.md` |
| Write configuration reference | 4h | Config section in guide |
| Write troubleshooting guide (fallback behavior, version mismatches) | 4h | Troubleshooting section |
| Add NumPy-style docstrings to all public functions | 4h | Docstrings in source code |

### Week 14: Agent Documentation (20h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Write agent-optimized API reference | 6h | `docs/AGENT_API_REFERENCE.md` |
| Create pattern cookbook (before/after examples) | 6h | `docs/AGENT_COOKBOOK.md` |
| Add cost annotations to all functions | 4h | HTTP call counts in docstrings |
| Write AGENTS.md section for the extension package | 4h | `metaflow-agent-client/AGENTS.md` |

**Pattern cookbook example:**

```markdown
## Finding failures

### Before (Client API, 56 HTTP calls)
```python
failures = []
for step in Run("MyFlow/42"):
    for task in step:
        if not task.successful:
            failures.append(task.pathspec)
# 56 HTTP calls, ~1700ms
```

### After (Agent utilities, 2-4 HTTP calls)
```python
from metaflow_extensions.agent_client.agent_query import find_failures
result = find_failures("MyFlow/42")
# result["failures"] == ["MyFlow/42/process/110"]
# 2-4 HTTP calls, ~40ms
```
```

### Week 15: PR Preparation (20h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Split implementation into logical PRs | 4h | PR plan document |
| PR 1: Metadata service `_limit` + `_order` | 4h | Ready for review |
| PR 2: `ServiceMetadataProvider` query param support | 4h | Ready for review |
| PR 3: `metaflow-agent-client` extension package | 4h | Ready for review |
| Run `pre-commit` / `black` on all code, verify tests pass | 4h | Clean code |

**PR format (per CONTRIBUTING.md):**

```markdown
## Summary
Add _limit and _order query parameters to metadata service listing endpoints.

## Context / Motivation
Agent workloads need bounded queries. The current API returns all results
for every listing request, forcing O(N) client-side filtering. This adds
optional server-side limiting and ordering. Fixes #NNN.

## Changes Made
- Added _limit and _order query param parsing to /runs and /tasks endpoints
- SQL queries now include LIMIT and ORDER BY when params are present
- No change when params are absent (backward compatible)

## Testing
- Added test_query_params.py (10 test cases)
- Manually verified: curl "http://localhost:8080/flows/X/runs?_limit=3"
- Tested backward compat: old client against new service
```

### Week 16: Final Report and Submission (20h)

| Task | Hours | Deliverable |
|------|-------|-------------|
| Write GSoC final report | 6h | Final report |
| Record demo video (3-5 minutes) | 4h | Demo video |
| Create before/after comparison table with real benchmarks | 4h | Summary table |
| Address remaining mentor feedback | 4h | Updated code/docs |
| Submit deliverables | 2h | All PRs and docs submitted |

**Phase 4 deliverables:**
- User guide, configuration reference, troubleshooting guide
- Agent API reference, pattern cookbook, system prompt fragment
- All PRs formatted per CONTRIBUTING.md, ready for review
- GSoC final report and demo video

---

## Summary Table

| Phase | Weeks | Hours | Key Deliverable |
|-------|-------|-------|-----------------|
| Foundation | 1-4 | 90h | Extension package with 6 agent functions |
| Core Implementation | 5-8 | 90h | Metadata service query params + provider enhancement |
| Agent Integration | 9-12 | 90h | Agent simulation, tool schemas, robustness tests |
| Documentation | 13-16 | 80h | User guide, agent docs, PR preparation, final report |
| **Total** | **16** | **350h** | |

## Risk Management

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Mentor rejects core changes | Medium | High | Extension package (Phase 1) delivers value independently |
| Status computation too complex | High | Medium | Document as future work, use UI Backend path |
| Metadata service and client version mismatch | Low | Low | Version-gated fallback (existing pattern) |
| UI Backend not deployed in user's environment | Medium | Low | Fallback path always works |
| Timeline too ambitious | Low | Medium | Each phase is independently valuable |

Each phase produces standalone, testable, demonstrable deliverables. If any phase takes longer than planned, the previous phase's output is still useful. The extension package from Phase 1 alone justifies the project.
