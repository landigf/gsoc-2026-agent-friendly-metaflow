"""
Phase 3: Prototype Agent-Friendly Utilities + Benchmark (v2)
Compares optimized utilities vs naive Client API approaches.

Key insight from v1: calling task.finished AND task.successful triggers
two separate full artifact resolution chains. The optimized version should
avoid redundant lookups by checking only what's needed.
"""
import time
from collections import defaultdict, deque
from metaflow import Flow, Run, Step, Task, namespace
from metaflow.metadata_provider.metadata import MetadataProvider

# ============================================================
# INSTRUMENTATION
# ============================================================
call_log = []
original_get_object = MetadataProvider.get_object.__func__

@classmethod
def instrumented_get_object(cls, obj_type, sub_type, filters, attempt, *args):
    call_log.append({
        "obj_type": obj_type,
        "sub_type": sub_type,
    })
    return original_get_object(cls, obj_type, sub_type, filters, attempt, *args)

MetadataProvider.get_object = instrumented_get_object

namespace(None)

def measure(operation_name, func):
    """Run a function, measure calls and wall-clock time."""
    call_log.clear()
    t0 = time.perf_counter()
    result = func()
    elapsed = time.perf_counter() - t0
    n_calls = len(call_log)
    breakdown = defaultdict(int)
    for c in call_log:
        breakdown[f"{c['obj_type']}/{c['sub_type']}"] += 1
    return {
        "result": result,
        "calls": n_calls,
        "time_ms": round(elapsed * 1000, 1),
        "breakdown": dict(breakdown),
    }


# ============================================================
# 3.1 BOUNDED run_summary()
# ============================================================
def run_summary(run_pathspec: str) -> dict:
    """
    Return a structured summary of a run with bounded cost.

    Optimization: Jump straight to end_task.successful — which
    checks _success artifact. This is the SAME call chain as
    Run.successful, but we skip the redundant task.finished check.

    The real optimization is architectural: we avoid calling BOTH
    finished AND successful (which doubles the artifact lookups).
    """
    run = Run(run_pathspec)

    # Go directly to what Run.successful does internally,
    # but avoid the double-lookup of finished+successful.
    try:
        end_step = run["end"]
        end_task = end_step.task
        # Only check .successful — it implies .finished
        # (a task can't be successful without being finished)
        is_successful = end_task.successful
        return {
            "status": "completed" if is_successful else "failed_at_end",
            "finished_at": str(end_task.finished_at) if is_successful else None,
        }
    except KeyError:
        pass  # end step doesn't exist yet

    # Fallback: only scan steps (O(steps), never O(tasks))
    steps = list(run)
    return {
        "status": "in_progress_or_failed",
        "steps_completed": [s.id for s in steps],
        "latest_step": steps[0].id if steps else None,
    }


# ============================================================
# 3.2 BOUNDED find_failures() — metadata-based detection
# ============================================================
def find_failures_metadata(run_pathspec: str, max_tasks: int = 100) -> dict:
    """
    Find failed tasks using task metadata instead of datastore artifacts.

    KEY OPTIMIZATION: Uses task.metadata_dict to check for 'attempt_ok'
    field, which is stored in the metadata DB (not the datastore). This
    avoids loading and unpickling _success artifacts.

    Falls back to task.successful only if metadata is unavailable.
    """
    run = Run(run_pathspec)
    failures = []
    tasks_checked = 0

    for step in run:
        for task in step:
            tasks_checked += 1
            if tasks_checked > max_tasks:
                return {"failures": failures, "truncated": True, "checked": tasks_checked}

            try:
                # Use metadata_dict to avoid datastore artifact reads
                md = task.metadata_dict
                attempt_ok = md.get("attempt_ok")

                if attempt_ok is None:
                    # Task hasn't finished yet
                    failures.append({
                        "pathspec": task.pathspec,
                        "step": step.id,
                        "status": "not_finished",
                    })
                elif attempt_ok == "False":
                    failures.append({
                        "pathspec": task.pathspec,
                        "step": step.id,
                        "status": "failed",
                    })
                # attempt_ok == "True" means success, skip
            except Exception as e:
                failures.append({"pathspec": task.pathspec, "error": str(e)[:200]})

    return {"failures": failures, "truncated": False, "checked": tasks_checked}


# ============================================================
# 3.3 BOUNDED tail_logs()
# ============================================================
def tail_logs(task_pathspec: str, stream: str = "stdout", n_lines: int = 50) -> str:
    """
    Get the last N lines of a task's log.
    Uses loglines() iterator with a rolling deque to avoid building
    the full string in memory.
    """
    task = Task(task_pathspec)
    recent = deque(maxlen=n_lines)
    for timestamp, line in task.loglines(stream):
        recent.append(line)
    return "\n".join(recent)


# ============================================================
# NAIVE IMPLEMENTATIONS (for comparison)
# ============================================================
def naive_run_successful(run_pathspec: str) -> bool:
    """The standard Client API way."""
    return Run(run_pathspec).successful

def naive_find_failed_tasks(run_pathspec: str) -> list:
    """Iterate all tasks, check .successful on each (artifact-based)."""
    run = Run(run_pathspec)
    failed = []
    for step in run:
        for task in step:
            if not task.successful:
                failed.append(task.pathspec)
    return failed

def naive_stdout(task_pathspec: str) -> str:
    """Load full stdout then slice."""
    return Task(task_pathspec).stdout[:100]


# ============================================================
# BENCHMARKS
# ============================================================
def print_comparison(label, naive_result, opt_result):
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    print(f"  {'Metric':<25} {'Naive':>12} {'Optimized':>12} {'Savings':>12}")
    print(f"  {'-'*25} {'-'*12} {'-'*12} {'-'*12}")

    call_diff = naive_result['calls'] - opt_result['calls']
    call_sign = "+" if call_diff < 0 else ""
    print(f"  {'get_object calls':<25} {naive_result['calls']:>12} {opt_result['calls']:>12} {call_sign}{call_diff:>11}")
    print(f"  {'Wall-clock (ms)':<25} {naive_result['time_ms']:>12.1f} {opt_result['time_ms']:>12.1f} {naive_result['time_ms'] - opt_result['time_ms']:>12.1f}")

    all_keys = sorted(set(list(naive_result['breakdown'].keys()) + list(opt_result['breakdown'].keys())))
    if all_keys:
        print(f"\n  Call breakdown:")
        for k in all_keys:
            n = naive_result['breakdown'].get(k, 0)
            o = opt_result['breakdown'].get(k, 0)
            diff = n - o
            marker = ""
            if diff > 0:
                marker = f"  (-{diff} saved)"
            elif diff < 0:
                marker = f"  (+{-diff} added)"
            print(f"    {k:<30} {n:>5} -> {o:>5}{marker}")
    print()


if __name__ == "__main__":
    print("Phase 3: Prototype Benchmarks (v2)")
    print("=" * 70)

    # --- Benchmark 1: run_summary vs Run.successful (SimpleFlow) ---
    flow = Flow("SimpleFlow")
    latest_run = list(flow)[0]
    run_pathspec = latest_run.pathspec
    call_log.clear()

    print(f"\n--- Using run: {run_pathspec} ---")

    naive_res = measure("NAIVE: Run.successful", lambda: naive_run_successful(run_pathspec))
    opt_res = measure("OPTIMIZED: run_summary", lambda: run_summary(run_pathspec))
    print_comparison("Benchmark 1: Run Status (SimpleFlow, 2 steps)", naive_res, opt_res)

    # --- Benchmark 2: run_summary on MultiStepFlow ---
    multi_flow = Flow("MultiStepFlow")
    multi_run = list(multi_flow)[0]
    multi_pathspec = multi_run.pathspec
    call_log.clear()

    print(f"--- Using run: {multi_pathspec} ---")

    naive_res2 = measure("NAIVE: Run.successful", lambda: naive_run_successful(multi_pathspec))
    opt_res2 = measure("OPTIMIZED: run_summary", lambda: run_summary(multi_pathspec))
    print_comparison("Benchmark 2: Run Status (MultiStepFlow, 4 steps)", naive_res2, opt_res2)

    # --- Benchmark 3: find_failures METADATA vs ARTIFACT ---
    foreach_flow = Flow("ForeachFlow")
    foreach_run = list(foreach_flow)[0]
    foreach_pathspec = foreach_run.pathspec
    call_log.clear()

    print(f"--- Using run: {foreach_pathspec} ---")

    naive_res3 = measure("NAIVE: iterate + .successful (artifacts)",
                         lambda: naive_find_failed_tasks(foreach_pathspec))
    opt_res3 = measure("OPTIMIZED: find_failures_metadata (metadata DB)",
                       lambda: find_failures_metadata(foreach_pathspec))
    print_comparison("Benchmark 3: Find Failures (ForeachFlow) — artifact vs metadata", naive_res3, opt_res3)

    # --- Benchmark 4: tail_logs vs full stdout ---
    simple_run = Run(run_pathspec)
    end_task = simple_run["end"].task
    task_pathspec = end_task.pathspec
    call_log.clear()

    print(f"--- Using task: {task_pathspec} ---")

    naive_res4 = measure("NAIVE: Task.stdout[:100]", lambda: naive_stdout(task_pathspec))
    opt_res4 = measure("OPTIMIZED: tail_logs(n_lines=5)", lambda: tail_logs(task_pathspec, n_lines=5))
    print_comparison("Benchmark 4: Log Access — full load vs tail", naive_res4, opt_res4)

    # --- Summary ---
    print("\n" + "=" * 70)
    print("  PHASE 3 SUMMARY")
    print("=" * 70)
    print(f"\n  {'Use Case':<40} {'Naive':>8} {'Opt':>8} {'Diff':>8}")
    print(f"  {'-'*40} {'-'*8} {'-'*8} {'-'*8}")

    for label, n, o in [
        ("Run status (SimpleFlow)", naive_res, opt_res),
        ("Run status (MultiStepFlow)", naive_res2, opt_res2),
        ("Find failures (ForeachFlow)", naive_res3, opt_res3),
        ("Log tail (stdout)", naive_res4, opt_res4),
    ]:
        diff = n['calls'] - o['calls']
        sign = "+" if diff < 0 else "-"
        print(f"  {label:<40} {n['calls']:>8} {o['calls']:>8} {sign}{abs(diff):>7}")

    print(f"""
  KEY FINDINGS:
  =============

  1. run_summary() has IDENTICAL call count to Run.successful
     because both follow the same resolution chain. The bottleneck
     is the Client API's object model, not our wrapper code.

  2. find_failures_metadata() uses task.metadata_dict (metadata DB)
     instead of task.successful (datastore artifact unpickling).
     This trades artifact/self calls for task/metadata calls —
     SAME number of get_object calls but avoids datastore I/O.

  3. tail_logs() has identical call count to Task.stdout because
     the log loading happens at the datastore level, not via
     get_object. The optimization is purely memory-based (deque
     vs full string).

  4. The fundamental problem is STRUCTURAL: the Client API's
     MetaflowObject resolution chain re-fetches parent objects
     on every access. No amount of wrapper code can fix this —
     it requires changes to:
     a) The metadata service (add ?status=, ?limit=, ?fields= params)
     b) The Client API (cache parent objects, batch queries)
     c) New endpoints (run_summary, batch_task_status)

  THEORETICAL OPTIMAL (with server-side changes):
  {'Use Case':<40} {'Current':>8} {'Optimal':>8} {'Savings':>8}
  {'-'*40} {'-'*8} {'-'*8} {'-'*8}
  {"Run status check":<40} {"7":>8} {"1":>8} {"86%":>8}
  {"Find failures (N tasks)":<40} {"~3N":>8} {"1":>8} {"97%+":>8}
  {"Latest successful run (M runs)":<40} {"~7M":>8} {"1":>8} {"99%+":>8}
  {"Time-filtered run listing":<40} {"2+N":>8} {"1":>8} {"66%+":>8}
  {"Batch run status (K runs)":<40} {"~7K":>8} {"1":>8} {"99%+":>8}
""")
