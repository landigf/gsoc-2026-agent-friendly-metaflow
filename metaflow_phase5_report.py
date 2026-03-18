"""
Phase 5: Final quantitative measurements for GSoC proposal.
Measures bytes fetched vs bytes needed for data waste ratios.
"""
import json
import sys
import time
from collections import defaultdict

from metaflow import Flow, Run, Step, Task, namespace
from metaflow.metadata_provider.metadata import MetadataProvider

# ============================================================
# INSTRUMENTATION — track both calls AND response sizes
# ============================================================
call_log = []
original_get_object = MetadataProvider.get_object.__func__

@classmethod
def instrumented_get_object(cls, obj_type, sub_type, filters, attempt, *args):
    result = original_get_object(cls, obj_type, sub_type, filters, attempt, *args)
    # Estimate response size in bytes
    try:
        size = len(json.dumps(result, default=str))
    except Exception:
        size = 0
    call_log.append({
        "obj_type": obj_type,
        "sub_type": sub_type,
        "response_bytes": size,
    })
    return result

MetadataProvider.get_object = instrumented_get_object
namespace(None)


def measure(label, func, needed_bytes_estimate):
    """Run func, measure calls, bytes, time. needed_bytes_estimate is what the agent actually uses."""
    call_log.clear()
    t0 = time.perf_counter()
    result = func()
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    n_calls = len(call_log)
    total_bytes = sum(c["response_bytes"] for c in call_log)
    breakdown = defaultdict(lambda: {"calls": 0, "bytes": 0})
    for c in call_log:
        k = f"{c['obj_type']}/{c['sub_type']}"
        breakdown[k]["calls"] += 1
        breakdown[k]["bytes"] += c["response_bytes"]

    # Estimate actual result size
    try:
        result_bytes = len(json.dumps(result, default=str))
    except Exception:
        result_bytes = needed_bytes_estimate

    return {
        "result": result,
        "calls": n_calls,
        "ms": elapsed_ms,
        "fetched_bytes": total_bytes,
        "needed_bytes": needed_bytes_estimate,
        "result_bytes": result_bytes,
        "breakdown": dict(breakdown),
        "waste_ratio": round(total_bytes / max(needed_bytes_estimate, 1), 1),
    }


# Pre-resolve pathspecs
flow_simple = Flow("SimpleFlow")
latest_simple = list(flow_simple)[0]
simple_ps = latest_simple.pathspec

flow_multi = Flow("MultiStepFlow")
latest_multi = list(flow_multi)[0]
multi_ps = latest_multi.pathspec

flow_foreach = Flow("ForeachFlow")
latest_foreach = list(flow_foreach)[0]
foreach_ps = latest_foreach.pathspec

simple_run = Run(simple_ps)
end_task = simple_run["end"].task
task_ps = end_task.pathspec
call_log.clear()

# ============================================================
# MEASUREMENTS
# ============================================================

# 1. Run.successful — agent needs 1 boolean (4 bytes: "true")
m1 = measure("Run.successful (SimpleFlow)",
             lambda: Run(simple_ps).successful,
             needed_bytes_estimate=4)

# 2. Flow.latest_successful_run — agent needs 1 run pathspec (~30 bytes)
m2 = measure("Flow.latest_successful_run",
             lambda: Flow("SimpleFlow").latest_successful_run,
             needed_bytes_estimate=30)

# 3. Check all 3 runs' status — agent needs 3 booleans (~20 bytes)
m3 = measure("All runs .successful (3 runs)",
             lambda: [(r.id, r.successful) for r in Flow("SimpleFlow")],
             needed_bytes_estimate=20)

# 4. Find failed tasks (ForeachFlow) — agent needs list of failed task IDs (~100 bytes)
m4 = measure("Find failed tasks (ForeachFlow)",
             lambda: [t.pathspec for s in Run(foreach_ps) for t in s if not t.successful],
             needed_bytes_estimate=100)

# 5. List artifacts for end task — agent needs artifact names (~50 bytes)
m5 = measure("List artifacts for task",
             lambda: [(a.id, str(a.created_at)) for a in Task(task_ps)],
             needed_bytes_estimate=50)

# 6. Task.stdout[:100] — agent needs 100 bytes of text
m6 = measure("Task.stdout[:100]",
             lambda: Task(task_ps).stdout[:100],
             needed_bytes_estimate=100)

# 7. Runs from last 24h — agent needs filtered list (~60 bytes)
from datetime import datetime, timedelta
m7 = measure("Runs from last 24h",
             lambda: [r.id for r in Flow("SimpleFlow") if r.created_at > datetime.now() - timedelta(hours=24)],
             needed_bytes_estimate=60)

# 8. Run summary (MultiStepFlow) — agent needs status + timing (~80 bytes)
m8 = measure("Run.successful (MultiStepFlow 4-step)",
             lambda: Run(multi_ps).successful,
             needed_bytes_estimate=4)

# ============================================================
# PRINT RESULTS
# ============================================================
print("=" * 80)
print("  PHASE 5: QUANTITATIVE RESULTS FOR GSoC PROPOSAL")
print("=" * 80)

# --- Table 1: Request Amplification ---
print("\n  TABLE 1: REQUEST AMPLIFICATION")
print("  " + "-" * 76)
print(f"  {'Use Case':<40} {'Calls':>6} {'Optimal':>8} {'Amplification':>14}")
print("  " + "-" * 76)

amplification_data = [
    ("Run.successful (2-step)", m1, 1),
    ("Run.successful (4-step)", m8, 1),
    ("Flow.latest_successful_run", m2, 1),
    ("All 3 runs .successful", m3, 1),
    ("Find failed tasks (foreach 50)", m4, 1),
    ("List task artifacts", m5, 1),
    ("Task.stdout[:100]", m6, 1),
    ("Time-filtered run listing", m7, 1),
]
for label, m, optimal in amplification_data:
    amp = f"{m['calls']}x" if optimal == 1 else f"{m['calls']/optimal:.1f}x"
    print(f"  {label:<40} {m['calls']:>6} {optimal:>8} {amp:>14}")

# --- Table 2: Data Waste ---
print(f"\n\n  TABLE 2: DATA WASTE RATIO")
print("  " + "-" * 76)
print(f"  {'Use Case':<40} {'Fetched':>10} {'Needed':>10} {'Waste Ratio':>12}")
print("  " + "-" * 76)

for label, m, _ in amplification_data:
    fetched = f"{m['fetched_bytes']:,}B"
    needed = f"{m['needed_bytes']}B"
    ratio = f"{m['waste_ratio']}x"
    print(f"  {label:<40} {fetched:>10} {needed:>10} {ratio:>12}")

total_fetched = sum(m['fetched_bytes'] for _, m, _ in amplification_data)
total_needed = sum(m['needed_bytes'] for _, m, _ in amplification_data)
print("  " + "-" * 76)
print(f"  {'TOTAL':<40} {total_fetched:>9,}B {total_needed:>9}B {total_fetched/max(total_needed,1):.1f}x")

# --- Table 3: Wall-Clock ---
print(f"\n\n  TABLE 3: WALL-CLOCK TIMING")
print("  " + "-" * 76)
print(f"  {'Use Case':<40} {'Time (ms)':>10} {'Calls':>6} {'ms/call':>8}")
print("  " + "-" * 76)

for label, m, _ in amplification_data:
    ms_per_call = f"{m['ms']/max(m['calls'],1):.2f}"
    print(f"  {label:<40} {m['ms']:>10.1f} {m['calls']:>6} {ms_per_call:>8}")

# --- Table 4: Breakdown by call type ---
print(f"\n\n  TABLE 4: CALL TYPE BREAKDOWN (across all use cases)")
print("  " + "-" * 76)

all_types = defaultdict(lambda: {"calls": 0, "bytes": 0})
for _, m, _ in amplification_data:
    for k, v in m['breakdown'].items():
        all_types[k]["calls"] += v["calls"]
        all_types[k]["bytes"] += v["bytes"]

print(f"  {'Call Type':<30} {'Total Calls':>12} {'Total Bytes':>12} {'Avg Bytes':>12}")
print("  " + "-" * 76)
for k in sorted(all_types.keys()):
    v = all_types[k]
    avg = v["bytes"] // max(v["calls"], 1)
    print(f"  {k:<30} {v['calls']:>12} {v['bytes']:>11,}B {avg:>11,}B")

total_calls = sum(v["calls"] for v in all_types.values())
print("  " + "-" * 76)
print(f"  {'TOTAL':<30} {total_calls:>12} {total_fetched:>11,}B")

# --- Architecture Diagram ---
print(f"""

  ARCHITECTURE DIAGRAM: Where Inefficiencies Live
  ================================================

  Agent / LLM
      |
      |  "Is this run successful?"  (needs: 1 boolean)
      v
  ┌─────────────────────────────────────────────────────────────────┐
  │  CLIENT API LAYER  (metaflow/client/core.py)                   │
  │                                                                │
  │  Run.successful                                                │
  │    └─ self.end_task                                            │
  │         └─ self["end"]  ──────────────── get_object(run, self) │ ← REDUNDANT x4
  │              └─ Step.__getitem__                                │
  │                   └─ get_object(run, step)  ← fetches ALL steps│
  │                        └─ step.task                            │
  │                             └─ get_object(step, task)          │
  │                                  └─ task["_success"]           │
  │                                       └─ get_object(task, artifact) ← fetches ALL artifacts
  │                                            └─ artifact.data   │
  │                                                 │              │
  │  PROBLEM: 7 get_object calls, 4 redundant       │              │
  │  parent re-fetches, all for 1 boolean            │              │
  └──────────────────────────────────────────────────┼──────────────┘
                                                     │
                                                     v
  ┌─────────────────────────────────────────────────────────────────┐
  │  METADATA SERVICE  (metaflow-service/metadata_service/)        │
  │                                                                │
  │  GET /flows/X/runs/Y         → returns FULL run JSON           │
  │  GET /flows/X/runs/Y/steps   → returns ALL steps (no filter)   │
  │  GET .../tasks               → returns ALL tasks (no filter)   │
  │  GET .../artifacts/Z         → returns artifact metadata       │
  │                                                                │
  │  PROBLEM: No ?status=, ?limit=, ?fields= params               │
  │  Every GET returns unbounded full JSON                         │
  │                                                                │
  │  NOTE: UI Backend Service HAS these features (_limit, _order,  │
  │  status filtering, ts_epoch filtering) but Client API doesn't  │
  │  use it — it talks only to the bare Metadata Service           │
  └──────────────────────────────────────────────────┬──────────────┘
                                                     │
                                                     v
  ┌─────────────────────────────────────────────────────────────────┐
  │  DATASTORE LAYER  (S3 / local filesystem)                      │
  │                                                                │
  │  artifact.data → loads + unpickles _success from datastore     │
  │  task.stdout   → loads ENTIRE log file from datastore          │
  │                                                                │
  │  PROBLEM: No byte-range reads, no server-side grep             │
  │  Success status stored as pickled boolean, not queryable       │
  └─────────────────────────────────────────────────────────────────┘

  EXISTING SERVER-SIDE CAPABILITY (underutilized):
  ┌─────────────────────────────────────────────────────────────────┐
  │  filter_tasks_by_metadata (metadata_service/api/task.py)       │
  │    GET .../filtered_tasks?metadata_field_name=X&pattern=Y      │
  │    → SQL: regexp_match(value, %s) on metadata table            │
  │    → 34 calls → 0 calls for failure detection                  │
  │                                                                │
  │  attempt_ok metadata field (task.py:974)                       │
  │    → Written to metadata DB on every task completion           │
  │    → value="True" or "False"                                   │
  │    → Queryable via SQL, but Client API ignores it              │
  └─────────────────────────────────────────────────────────────────┘

  PROPOSED SOLUTION (GSoC):
  ┌─────────────────────────────────────────────────────────────────┐
  │  Agent-Friendly Client                                         │
  │    └─ AgentClient.run_status(pathspec) ──→ 1 call              │
  │    └─ AgentClient.failed_tasks(pathspec) ──→ 1 call            │
  │    └─ AgentClient.recent_runs(flow, limit=10) ──→ 1 call      │
  │    └─ AgentClient.tail_logs(task, n=50) ──→ 1 call             │
  │                                                                │
  │  Backed by:                                                    │
  │    ├─ Enhanced metadata service endpoints (?limit, ?status)    │
  │    ├─ Batch query support (multiple tasks in 1 call)           │
  │    ├─ MetaflowObject caching (eliminate redundant lookups)     │
  │    └─ MCP tool definitions for LLM tool-use                   │
  └─────────────────────────────────────────────────────────────────┘
""")

# --- AgentMetadataProvider comparison ---
print(f"\n  TABLE 5: AGENT PROVIDER vs NAIVE (from Phase 4)")
print("  " + "-" * 76)
print(f"  {'Use Case':<40} {'Naive':>7} {'Agent':>7} {'Saved':>7} {'%':>6}")
print("  " + "-" * 76)
phase4 = [
    ("Recent runs (limit=3)",              2,   2,  0),
    ("Run summary",                        7,   9, -2),
    ("Find failures (metadata vs artifact)", 34, 25,  9),
    ("Batch run status (3 runs)",         20,  20,  0),
    ("Log tail",                           4,   4,  0),
    ("Time-filtered runs",                 2,   2,  0),
    ("Server-side failure detection",     34,   0, 34),
]
tn = to = 0
for label, n, o, s in phase4:
    tn += n; to += o
    pct = f"{(s/max(n,1))*100:.0f}%" if s > 0 else "-"
    print(f"  {label:<40} {n:>7} {o:>7} {s:>7} {pct:>6}")
print("  " + "-" * 76)
ts = tn - to
print(f"  {'TOTAL':<40} {tn:>7} {to:>7} {ts:>7} {(ts/max(tn,1))*100:.0f}%")

# --- Theoretical optimal ---
print(f"\n\n  TABLE 6: THEORETICAL OPTIMAL (with full server-side support)")
print("  " + "-" * 76)
print(f"  {'Use Case':<40} {'Current':>8} {'Optimal':>8} {'Reduction':>10}")
print("  " + "-" * 76)
theoretical = [
    ("Run status check",                    7,  1, "86%"),
    ("Latest successful run",               8,  1, "88%"),
    ("All N runs' status",            "~7N",  1, "99%+"),
    ("Find failures (N tasks)",       "~3N",  1, "97%+"),
    ("List task artifacts",                 8,  1, "88%"),
    ("Task stdout (tail N lines)",          4,  1, "75%"),
    ("Time-filtered run listing",           2,  1, "50%"),
    ("Batch run status (K runs)",     "~7K",  1, "99%+"),
]
for label, cur, opt, pct in theoretical:
    print(f"  {label:<40} {str(cur):>8} {opt:>8} {pct:>10}")

print("\n" + "=" * 80)
print("  END OF PHASE 5 — All data ready for GSoC proposal")
print("=" * 80)
