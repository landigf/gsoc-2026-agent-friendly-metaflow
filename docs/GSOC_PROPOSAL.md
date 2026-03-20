# [GSOC 2026 Proposal]: Agent-Friendly Metaflow Client

---

## 1. About Me

**Name:** [landigf]
**University:** ETH Zurich
**Program:** MSc Computer Science
**Lab:** EASL (Efficient Architectures and Systems Lab)
**Year:** 2nd year

**Technical background:**

My research at EASL focuses on infrastructure pressure from AI and agentic workloads — specifically how agent-driven programmatic access patterns differ from human-interactive ones and what infrastructure changes are needed to support them efficiently. This GSoC project is directly aligned with my research.

**Relevant skills:**
- Python (advanced): 4+ years, including library internals, decorators, metaclasses, monkeypatching for instrumentation
- REST APIs and database-backed services: built and profiled service layers in coursework and research
- Performance analysis: request counting, response size estimation, call chain tracing
- Open source: [link to GitHub profile / relevant PRs]

**Why this project:**

I've been using Metaflow for ML experiment tracking and noticed that when AI agents interact with the Client API, they trigger far more HTTP calls than a human would. I traced the root cause through the codebase and discovered that the metadata service already has the infrastructure to answer agent queries efficiently — it's just not exposed. This proposal is based on that hands-on investigation, not on reading the project description.

---

## 2. Project Description

**Project:** Agent-Friendly Metaflow Client: Analyzing and Addressing Client API Inefficiencies
**Mentor:** Valay Dave
**Difficulty:** Hard | **Duration:** 350 hours

### Problem understanding

The Metaflow Client API (`Flow`, `Run`, `Step`, `Task`) translates user operations into HTTP requests against the metadata service via `ServiceMetadataProvider`. The translation is implicit — a simple `task.successful` check triggers an artifact fetch, a datastore read, and an unpickle operation per task. For a 50-task foreach, "which tasks failed?" costs 56 HTTP calls and ~3.8 seconds.

This matters now because AI agents are becoming primary consumers of the Client API. Unlike humans who browse one object at a time, agents ask questions that require scanning across runs, steps, and tasks. The current API has no pagination, no server-side status filtering, and no bounded log access — patterns that didn't matter for interactive use but are critical for programmatic access.

### Key discovery

The metadata service already stores task success/failure as `attempt_ok` in the `metadata_v3` table, and since v2.5.0 exposes `filter_tasks_by_metadata` which can query it. The database layer (`postgres_async_db.py`) already supports `LIMIT`, `OFFSET`, and `ORDER BY` through `find_records()` — the HTTP endpoints just don't pass these parameters through.

This means the fix doesn't require a new service or complex infrastructure. It requires:
1. Smart utility functions that use existing but underutilized capabilities
2. ~150 lines of changes to expose what the DB layer already supports
3. Version-gated Client API enhancements that use the new parameters automatically

### System design

```
Layer 1: metaflow-agent-client extension package
         Smart functions using filter_tasks_by_metadata + bounded iteration
         Works today on any deployment with metadata service >= 2.5.0

Layer 2: Metadata service endpoint improvements (~150 lines)
         Expose _limit, _order (DB already supports them)
         Add simplified status endpoint via attempt_ok
         Benefits all users, not just agents

Layer 3: ServiceMetadataProvider enhancement
         Version-gated: auto-detect service capabilities
         Pass _limit/_order when supported, fall back when not
         Existing code gets faster transparently
```

---

## 3. Technical Approach

### Prior codebase exploration (already completed)

I set up the full local dev stack (Postgres, metadata service, UI backend via minikube), created test flows (SimpleFlow, ForeachFlow with 50 tasks and intentional failure, MultiStepFlow), and instrumented the Client API by monkeypatching `ServiceMetadataProvider._request` to count every HTTP call.

**Benchmarks completed (all 6 GSoC use cases):**

| UC | Use Case | Client API | Smart Path | Speedup |
|----|----------|-----------|------------|---------|
| 1 | List runs by status | 4 calls | 1 call | 6.7x |
| 2 | Filter runs by time range | 2 calls | 1 call | 1.5x |
| 3 | Find failed tasks + errors | 57 calls | 5 calls | 19.1x |
| 4 | Artifact metadata | 5 calls | 4 calls | ~same |
| 5 | Bounded log output | 6 calls | 4 calls | ~same |
| 6 | Cross-run artifact search | 8 calls | 5 calls | 1.8x |

UC3 is the dominant bottleneck (70% of all calls). UC4/UC5 are not about call count but about bounded data — `task.stdout` loads the entire log as a string; there's no cross-task artifact listing.

**Three-path benchmark (finding failed tasks):**

| Path | HTTP Calls | Time | Infrastructure |
|------|-----------|------|----------------|
| A: Naive Client API (today) | 56 | 3,800ms | Metadata service |
| C: Smart Metadata (our approach) | 4 | 349ms | Metadata service |
| B: UI Backend (bonus) | 2 | 35ms | Metadata service + UI Backend |

All three return the identical result. I built a Metaflow flow (`BenchmarkThreePaths`) that runs all three as parallel branches and is visible in the Metaflow UI as a DAG.

### Component-by-component plan

**Extension package (`metaflow-agent-client`):**
- Uses `metaflow_extensions` mechanism (no core changes)
- 6 utility functions: `find_failures()`, `get_recent_runs()`, `batch_run_status()`, `run_summary()`, `tail_logs()`, `get_runs_since()`
- Each function: fast path (uses `filter_tasks_by_metadata` or UI Backend when available) + fallback (standard Client API)
- Automated tests verify both paths return identical results

**Metadata service changes:**
- Add `_limit` and `_order` query param parsing to `api/run.py` and `api/task.py` (~15 lines each)
- Wire to existing `find_records()` which already accepts these params
- Add simplified status endpoint using `attempt_ok` metadata (~40 lines)
- Bump service version for client detection

**ServiceMetadataProvider enhancement:**
- Add `_supports_query_params` version flag (follows `_supports_attempt_gets` pattern at `service.py:260`)
- In `_get_object_internal`, append `?_limit=N&_order=-ts_epoch` for listing operations when supported
- Fall back to current behavior on older services

**Key technical decisions:**
- Extension package first (no core changes, fast iteration) → then metadata service → then provider
- `filter_tasks_by_metadata` for failure detection instead of porting the UI Backend's 300-line status SQL
- Version-gated enhancements following the established `_supports_*` pattern
- UI Backend as optional bonus, not a dependency

---

## 4. Challenges & Mitigation

**Challenge 1: Core Runtime gate.**
Changes to `metadata_provider/` require pre-approved issues and maintainer alignment. *Mitigation:* Start with the extension package (Layer 1), which needs zero core changes. Present benchmarks to mentor early to align on the metadata service changes.

**Challenge 2: Status computation complexity.**
The UI Backend's task status is a 300-line SQL CASE with lateral JOINs. *Mitigation:* Don't port it. Use the simplified approach: `attempt_ok = True → completed, False → failed, else → running`. This covers 90% of cases. Document the edge cases (stale heartbeats, old runs) as future work.

**Challenge 3: Two-repo coordination.**
Metadata service changes (metaflow-service) and client changes (metaflow) need coordinated versioning. *Mitigation:* Version-gated detection. Client checks service version at init, uses new params when supported, falls back when not. Both sides work independently.

**Challenge 4: Backward compatibility.**
*Mitigation:* All changes are additive. New query params are optional (absent = current behavior). Version checks follow the established pattern. Tested across {old service, new service} x {with UI Backend, without}.

**If behind schedule:** Phases are independent. Layer 1 (extension package) alone is a complete, useful deliverable. Layer 2 (metadata service) adds value on top. Layer 3 (provider enhancement) is the cherry on top.

---

## 5. Weekly Timeline

### Phase 1: Foundation (Weeks 1–4, ~90h)

| Week | Deliverable |
|------|------------|
| 1 | Extension package scaffold using `metaflow_extensions`. Port prototype. Unit tests with mocked HTTP. Present RFC to mentor. |
| 2 | UI Backend HTTP client with fallback detection. `METAFLOW_UI_BACKEND_URL` config. Connection pooling following `requests.Session` pattern from `service.py`. |
| 3 | Core functions: `get_recent_runs()`, `find_failures()`, `batch_run_status()`. Integration tests against dev stack. Automated fast-path == fallback-path comparison. |
| 4 | Remaining functions: `run_summary()`, `tail_logs()`, `get_runs_since()`. Benchmark harness. All 6 functions tested. |

**Midterm checkpoint:** Extension package with 6 working functions, all tested, benchmark showing improvement.

### Phase 2: Core Implementation (Weeks 5–8, ~90h)

| Week | Deliverable |
|------|------------|
| 5 | Add `_limit` + `_order` to metadata service `/runs` and `/tasks` endpoints. Service-level tests. |
| 6 | `ServiceMetadataProvider`: add `_supports_query_params` version flag. Pass params in `_get_object_internal`. Unit tests mocking different versions. |
| 7 | Evaluate simplified status endpoint. If feasible, implement. If not, document as future work with analysis. |
| 8 | End-to-end integration. Backward compat matrix: {old, new} x {with UI, without}. Performance regression check. |

### Phase 3: Agent Integration (Weeks 9–12, ~90h)

| Week | Deliverable |
|------|------------|
| 9 | Agent simulation framework: 5 scripted scenarios measuring calls + latency, old vs new. |
| 10 | Tool-calling integration: JSON schema / MCP tool definitions. Test with an LLM agent to validate. |
| 11 | Robustness: 1000+ task foreach, network failures, partial availability, concurrent access. |
| 12 | Performance validation on realistic dataset. Profiling. Comparison report. |

### Phase 4: Documentation & Polish (Weeks 13–16, ~80h)

| Week | Deliverable |
|------|------------|
| 13 | User guide: "Using Metaflow with AI Agents". Config docs. NumPy-style docstrings. |
| 14 | Agent API reference (JSON schema). Pattern cookbook (before/after). Cost annotations. |
| 15 | **Code freeze.** PR preparation: split into logical PRs per CONTRIBUTING.md. `pre-commit` / `black`. |
| 16 | Final report. Demo video. Address mentor feedback. Submit. |

---

## 6. Other Commitments

- **Exams:** [list any exam periods during May–August]
- **Work:** No other employment during GSoC period
- **Vacations:** [list any planned time off]
- **Weekly availability:** 22+ hours/week (350h / 16 weeks)

---

## Attachments

- **Presentation:** GSoC_2026_Agent_Friendly_Metaflow.pptx (21 slides following Metaflow's visual style)
- **Demo video:** [YouTube link — 60-second benchmark demo]
- **Benchmark code:** All scripts at `demo/` (benchmark_three_paths.py, full_audit.py, etc.)
- **Documentation:** RFC, implementation plan, project overview at `docs/`
- **Metaflow UI screenshot:** [attach screenshot of BenchmarkThreePaths DAG from localhost:3000]

---

## Disclosure

AI tools were used to assist with:
- Codebase exploration and search
- Benchmark script scaffolding
- Presentation generation (python-pptx)
- Document drafting

All code, analysis, architectural decisions, and benchmark interpretation are my own work. I can explain every line and every design choice independently.
