"""
Phase 4: AgentMetadataProvider Benchmark
Compares agent-friendly utilities vs naive Client API for all use cases.
"""
import time
import sys
from collections import defaultdict

sys.path.insert(0, "/Users/landigf/Desktop/Code/GSoC")

from metaflow import Flow, Run, Step, Task, namespace
from metaflow.metadata_provider.metadata import MetadataProvider
from metaflow_agent import AgentMetadataProvider

# ============================================================
# INSTRUMENTATION
# ============================================================
call_log = []
original_get_object = MetadataProvider.get_object.__func__

@classmethod
def instrumented_get_object(cls, obj_type, sub_type, filters, attempt, *args):
    call_log.append({"obj_type": obj_type, "sub_type": sub_type})
    return original_get_object(cls, obj_type, sub_type, filters, attempt, *args)

MetadataProvider.get_object = instrumented_get_object
namespace(None)


def measure(label, func):
    call_log.clear()
    t0 = time.perf_counter()
    result = func()
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    n = len(call_log)
    bd = defaultdict(int)
    for c in call_log:
        bd[f"{c['obj_type']}/{c['sub_type']}"] += 1
    return {"result": result, "calls": n, "ms": elapsed_ms, "breakdown": dict(bd)}


def print_bench(title, naive, opt):
    diff = naive["calls"] - opt["calls"]
    pct = f"{(diff / max(naive['calls'],1)) * 100:.0f}%" if diff > 0 else "N/A"
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")
    print(f"  {'':25s} {'Naive':>10} {'Agent':>10} {'Saved':>10}")
    print(f"  {'-'*25} {'-'*10} {'-'*10} {'-'*10}")
    print(f"  {'get_object calls':25s} {naive['calls']:>10} {opt['calls']:>10} {diff:>10}")
    print(f"  {'Wall-clock (ms)':25s} {naive['ms']:>10.1f} {opt['ms']:>10.1f} {naive['ms'] - opt['ms']:>10.1f}")
    if diff > 0:
        print(f"  {'Reduction':25s} {'':>10} {'':>10} {pct:>10}")

    all_keys = sorted(set(list(naive['breakdown'].keys()) + list(opt['breakdown'].keys())))
    print(f"\n  Breakdown:")
    for k in all_keys:
        n = naive['breakdown'].get(k, 0)
        o = opt['breakdown'].get(k, 0)
        d = n - o
        tag = ""
        if d > 0: tag = f" (-{d})"
        elif d < 0: tag = f" (+{-d})"
        print(f"    {k:<30} {n:>5} -> {o:>5}{tag}")


agent = AgentMetadataProvider()

# ============================================================
# Pre-resolve flow/run pathspecs (don't count these calls)
# ============================================================
flow_simple = Flow("SimpleFlow")
latest_simple = list(flow_simple)[0]
simple_ps = latest_simple.pathspec

flow_multi = Flow("MultiStepFlow")
latest_multi = list(flow_multi)[0]
multi_ps = latest_multi.pathspec

flow_foreach = Flow("ForeachFlow")
latest_foreach = list(flow_foreach)[0]
foreach_ps = latest_foreach.pathspec

# Get a task pathspec for log benchmarks
simple_run = Run(simple_ps)
end_task = simple_run["end"].task
task_ps = end_task.pathspec

call_log.clear()

print("=" * 70)
print("  PHASE 4: AgentMetadataProvider Benchmark")
print("=" * 70)

# ============================================================
# Benchmark 1: Recent runs listing
# ============================================================
naive1 = measure("NAIVE: list(Flow('SimpleFlow'))[:3]",
                 lambda: [{"id": r.id} for i, r in enumerate(Flow("SimpleFlow")) if i < 3])
opt1 = measure("AGENT: get_recent_runs limit=3",
               lambda: agent.get_recent_runs("SimpleFlow", limit=3))
print_bench("1. Recent Runs (limit=3)", naive1, opt1)

# ============================================================
# Benchmark 2: Run summary
# ============================================================
naive2 = measure("NAIVE: Run.successful",
                 lambda: Run(simple_ps).successful)
opt2 = measure("AGENT: run_summary",
               lambda: agent.run_summary(simple_ps))
print_bench("2. Run Summary (SimpleFlow)", naive2, opt2)

# ============================================================
# Benchmark 3: Find failures — artifact vs metadata
# ============================================================
naive3 = measure("NAIVE: iterate + .successful (artifact)",
                 lambda: [t.pathspec for s in Run(foreach_ps) for t in s if not t.successful])
opt3 = measure("AGENT: find_failures (metadata)",
               lambda: agent.find_failures(foreach_ps, use_metadata=True))
print_bench("3. Find Failures (ForeachFlow) — artifact vs metadata", naive3, opt3)

# ============================================================
# Benchmark 4: Batch run status
# ============================================================
naive4 = measure("NAIVE: iterate runs + .successful",
                 lambda: [(r.id, r.successful) for i, r in enumerate(Flow("SimpleFlow")) if i < 3])
opt4 = measure("AGENT: batch_run_status limit=3",
               lambda: agent.batch_run_status("SimpleFlow", limit=3))
print_bench("4. Batch Run Status (3 runs)", naive4, opt4)

# ============================================================
# Benchmark 5: Log tail
# ============================================================
naive5 = measure("NAIVE: Task.stdout[:100]",
                 lambda: Task(task_ps).stdout[:100])
opt5 = measure("AGENT: tail_logs(n_lines=5)",
               lambda: agent.tail_logs(task_ps, n_lines=5))
print_bench("5. Log Tail", naive5, opt5)

# ============================================================
# Benchmark 6: Time-filtered runs
# ============================================================
naive6 = measure("NAIVE: iterate + check created_at",
                 lambda: [r.id for r in Flow("SimpleFlow")
                          if (r.created_at.__class__.__name__ != "NoneType")])
opt6 = measure("AGENT: get_runs_since(hours=24)",
               lambda: agent.get_runs_since("SimpleFlow", hours=24))
print_bench("6. Time-filtered Runs (last 24h)", naive6, opt6)

# ============================================================
# Benchmark 7: Server-side failure detection (filter_tasks_by_metadata)
# ============================================================
# Extract run_id from foreach pathspec
foreach_run_id = foreach_ps.split("/")[1]

naive7 = measure("NAIVE: iterate tasks + check .successful",
                 lambda: agent.find_failures(foreach_ps, use_metadata=False))
opt7 = measure("AGENT: filter_tasks_by_metadata (server-side)",
               lambda: agent.find_failed_tasks_server_side("ForeachFlow", foreach_run_id, "process"))
print_bench("7. Server-Side Failure Detection (filter_tasks_by_metadata)", naive7, opt7)


# ============================================================
# GRAND SUMMARY
# ============================================================
print("\n\n" + "=" * 70)
print("  GRAND SUMMARY: Agent-Friendly Provider vs Naive Client API")
print("=" * 70)
print(f"\n  {'#':<4} {'Use Case':<40} {'Naive':>7} {'Agent':>7} {'Saved':>7} {'%':>6}")
print(f"  {'-'*4} {'-'*40} {'-'*7} {'-'*7} {'-'*7} {'-'*6}")

benchmarks = [
    ("1", "Recent runs (limit=3)", naive1, opt1),
    ("2", "Run summary", naive2, opt2),
    ("3", "Find failures (metadata vs artifact)", naive3, opt3),
    ("4", "Batch run status (3 runs)", naive4, opt4),
    ("5", "Log tail", naive5, opt5),
    ("6", "Time-filtered runs", naive6, opt6),
    ("7", "Server-side failure detection", naive7, opt7),
]

total_naive = 0
total_opt = 0
for num, label, n, o in benchmarks:
    diff = n['calls'] - o['calls']
    pct = f"{(diff / max(n['calls'],1)) * 100:.0f}%" if diff > 0 else "-"
    total_naive += n['calls']
    total_opt += o['calls']
    print(f"  {num:<4} {label:<40} {n['calls']:>7} {o['calls']:>7} {diff:>7} {pct:>6}")

total_saved = total_naive - total_opt
total_pct = f"{(total_saved / max(total_naive,1)) * 100:.0f}%"
print(f"  {'-'*4} {'-'*40} {'-'*7} {'-'*7} {'-'*7} {'-'*6}")
print(f"  {'':4s} {'TOTAL':40s} {total_naive:>7} {total_opt:>7} {total_saved:>7} {total_pct:>6}")

print(f"""

  ANALYSIS:
  =========
  The AgentMetadataProvider demonstrates two types of optimization:

  A) METADATA-BASED STATUS DETECTION (benchmarks 3, 7)
     By using task.metadata_dict (attempt_ok field from metadata DB)
     instead of task.successful (_success artifact from datastore),
     we eliminate all artifact/self calls. This avoids S3 reads and
     pickle deserialization — the most expensive operations.

  B) SERVER-SIDE FILTERING (benchmark 7)
     filter_tasks_by_metadata is the ONE existing server-side query
     capability. It filters tasks by metadata field + regex pattern
     entirely in Postgres. This reduces the call from O(tasks) to O(1).

  C) BOUNDED QUERIES (benchmarks 1, 4)
     Early termination via limit prevents loading entire run histories.
     With 500 runs, naive loads all 500; bounded loads only 3.

  D) UNCHANGED (benchmarks 2, 5, 6)
     Run summary and log access have identical call counts because the
     bottleneck is the Client API's object resolution chain — every
     lookup re-resolves parent objects. These require STRUCTURAL changes:
     - Caching in MetaflowObject.__init__
     - New server endpoints (/runs/{{id}}/summary, /tasks/{{id}}/logs?tail=N)
     - Query parameters on existing endpoints (?status=, ?limit=, ?fields=)

  These findings directly inform the GSoC implementation plan:
  Phase 1 (Weeks 1-3): Add ?_limit, ?_order, ?status to metadata service
  Phase 2 (Weeks 4-6): Build AgentClient with caching + batch queries
  Phase 3 (Weeks 7-9): New endpoints (run_summary, batch_task_status)
  Phase 4 (Weeks 10-12): MCP/tool integration + documentation
""")
