#!/usr/bin/env python3
"""
Metaflow Client API Efficiency Audit for Agent Use Cases.

Instruments MetadataProvider.get_object (the base-class entry point shared by
both LocalMetadataProvider and ServiceMetadataProvider) to count and classify
the metadata queries triggered by common agent operations.

Each query in a local setup corresponds to a filesystem read; with a live
metadata service it maps 1-to-1 to an HTTP GET against the REST API.  The
call count is what matters for the efficiency argument — the transport is
interchangeable.

Author: Gennaro Francesco Landi (ETH Zürich / EASL)
Context: GSoC 2026 pre-application — Agent-Friendly Metaflow Client
"""
import functools
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from metaflow import Flow
from metaflow.metadata_provider.metadata import MetadataProvider


# ──────────────────────────────────────────────────────────────────────────────
# Instrumentation layer
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class QueryLog:
    """Records every call to MetadataProvider.get_object."""
    calls: List[dict] = field(default_factory=list)

    def record(self, obj_type: str, sub_type: str, args: tuple, duration_ms: float):
        # Build a human-readable "path" analogous to a REST endpoint
        parts = [obj_type]
        parts.extend(str(a) for a in args if a is not None)
        parts.append(sub_type)
        self.calls.append({
            "path": "/".join(parts),
            "obj_type": obj_type,
            "sub_type": sub_type,
            "duration_ms": round(duration_ms, 3),
        })

    def summary(self) -> dict:
        if not self.calls:
            return {"total_queries": 0}
        paths = Counter(c["path"] for c in self.calls)
        patterns = Counter(f"{c['obj_type']} → {c['sub_type']}" for c in self.calls)
        return {
            "total_queries": len(self.calls),
            "total_duration_ms": round(sum(c["duration_ms"] for c in self.calls), 1),
            "unique_patterns": len(patterns),
            "top_patterns": patterns.most_common(5),
            "top_paths": paths.most_common(3),
        }


@contextmanager
def instrumented(log: QueryLog):
    """
    Context manager that patches MetadataProvider.get_object for the duration
    of the with-block, then restores the original.

    Patching the base-class classmethod means all concrete providers
    (Local, Service, Spin) are intercepted without knowing which one is active.
    """
    original = MetadataProvider.get_object

    @classmethod  # type: ignore[misc]
    @functools.wraps(original.__func__)
    def patched(cls, obj_type, sub_type, filters, attempt, *args):
        t0 = time.perf_counter()
        result = original.__func__(cls, obj_type, sub_type, filters, attempt, *args)
        duration_ms = (time.perf_counter() - t0) * 1000
        log.record(obj_type, sub_type, args, duration_ms)
        return result

    MetadataProvider.get_object = patched
    try:
        yield log
    finally:
        MetadataProvider.get_object = original


# ──────────────────────────────────────────────────────────────────────────────
# Agent Use Cases
# ──────────────────────────────────────────────────────────────────────────────

def usecase_list_recent_runs(flow_name: str, n: int = 10) -> dict:
    """
    USE CASE 1: "Show me the last N runs of this flow."

    Current approach: iterate all runs, stop after N.
    Problem: the client fetches *all* runs from the provider with no server-side
    LIMIT/OFFSET — it filters in Python.  For a flow with 1000+ runs this means
    reading 1000 metadata records to return 10.
    """
    log = QueryLog()
    with instrumented(log):
        flow = Flow(flow_name)
        runs = []
        for i, run in enumerate(flow.runs()):
            runs.append(run.id)
            if i >= n - 1:
                break

    return {
        "use_case": "list_recent_runs",
        "flow": flow_name,
        "requested": n,
        "returned": len(runs),
        **log.summary(),
    }


def usecase_get_run_status(flow_name: str, run_id: str) -> dict:
    """
    USE CASE 2: "Did run X succeed?"

    Current approach: run.successful walks Run → Steps → Tasks checking each
    task's status in Python.  No direct "run status" endpoint exists.
    Expected O(steps + tasks) queries.
    """
    log = QueryLog()
    with instrumented(log):
        run = Flow(flow_name)[run_id]
        status = run.successful
        finished = run.finished

    return {
        "use_case": "get_run_status",
        "flow": flow_name,
        "run": run_id,
        "successful": status,
        "finished": finished,
        **log.summary(),
    }


def usecase_find_failed_tasks(flow_name: str, run_id: str) -> dict:
    """
    USE CASE 3: "Which tasks failed in run X?"

    Current approach: Run → Steps → Tasks; check .successful per task.
    Demonstrates the N+1 pattern: 1 query for steps + N_steps queries for tasks
    + N_steps*N_tasks queries to resolve each task's status.
    """
    log = QueryLog()
    failed = []
    with instrumented(log):
        run = Flow(flow_name)[run_id]
        for step in run.steps():
            for task in step.tasks():
                if not task.successful:
                    failed.append(f"{step.id}/{task.id}")

    return {
        "use_case": "find_failed_tasks",
        "flow": flow_name,
        "run": run_id,
        "failed_tasks": failed,
        **log.summary(),
    }


def usecase_time_filtered_runs(flow_name: str, hours: int = 24) -> dict:
    """
    USE CASE 4: "Show me runs from the last 24 hours."

    Current approach: fetch ALL runs, filter by created_at in Python.
    Problem: no server-side time-range predicate — the metadata service API has
    no ?created_after= parameter.  For high-frequency flows this is O(all runs).
    """
    import datetime
    log = QueryLog()
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
    recent = []
    with instrumented(log):
        flow = Flow(flow_name)
        for run in flow.runs():
            if run.created_at and run.created_at >= cutoff:
                recent.append(run.id)
            # Runs come in reverse-chronological order, but the API doesn't
            # guarantee this and there's no early-exit mechanism.

    return {
        "use_case": "time_filtered_runs",
        "flow": flow_name,
        "hours": hours,
        "found": len(recent),
        **log.summary(),
    }


def usecase_collect_run_artifacts(flow_name: str, run_id: str,
                                  artifact_name: str = "score") -> dict:
    """
    USE CASE 5: "Gather the 'score' artifact from every task in this run."

    This is a classic agent operation: scan all tasks and extract one value.
    Current approach: Run → Steps → Tasks → Artifacts; one query per level.
    Demonstrates the deepest N+1 chain in the client hierarchy.
    """
    log = QueryLog()
    values = {}
    with instrumented(log):
        run = Flow(flow_name)[run_id]
        for step in run.steps():
            for task in step.tasks():
                try:
                    val = getattr(task.data, artifact_name, None)
                    if val is not None:
                        values[f"{step.id}/{task.id}"] = val
                except Exception:
                    pass

    return {
        "use_case": "collect_run_artifacts",
        "flow": flow_name,
        "run": run_id,
        "artifact": artifact_name,
        "found": len(values),
        **log.summary(),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Report
# ──────────────────────────────────────────────────────────────────────────────

def _print_result(result: dict):
    uc = result["use_case"]
    n_q = result.get("total_queries", 0)
    ms = result.get("total_duration_ms", 0)
    print(f"\n┌─ {uc}")
    print(f"│  queries : {n_q}")
    print(f"│  latency : {ms:.1f} ms total")
    for k, v in result.items():
        if k in ("use_case", "total_queries", "total_duration_ms",
                  "unique_patterns", "top_patterns", "top_paths", "flow"):
            continue
        print(f"│  {k:12s}: {v}")
    if result.get("top_patterns"):
        print("│  query patterns (count × obj_type → sub_type):")
        for pat, cnt in result["top_patterns"]:
            print(f"│    {cnt:3d}×  {pat}")
    print("└" + "─" * 50)


def run_audit(flow_name: str):
    print("=" * 60)
    print("  Metaflow Client API — Agent Use-Case Efficiency Audit")
    print(f"  Flow : {flow_name}")
    print("=" * 60)

    flow = Flow(flow_name)
    latest_run = next(iter(flow.runs()), None)
    if latest_run is None:
        print("No runs found. Run the flow at least once first.")
        return
    run_id = latest_run.id
    print(f"  Using run : {run_id}\n")

    results = [
        usecase_list_recent_runs(flow_name, n=5),
        usecase_get_run_status(flow_name, run_id),
        usecase_find_failed_tasks(flow_name, run_id),
        usecase_time_filtered_runs(flow_name, hours=24),
        usecase_collect_run_artifacts(flow_name, run_id),
    ]

    for r in results:
        _print_result(r)

    # Summary table
    print("\n" + "=" * 60)
    print("  SUMMARY  (queries = metadata provider calls per operation)")
    print("=" * 60)
    print(f"  {'Use case':<35} {'Queries':>8}  {'ms':>8}")
    print("  " + "-" * 55)
    for r in results:
        print(f"  {r['use_case']:<35} {r.get('total_queries', 0):>8}  "
              f"{r.get('total_duration_ms', 0):>7.1f}")
    print()
    print("  Each query = 1 HTTP GET against the metadata service REST API.")
    print("  Ideal agent-friendly targets (to implement in GSoC):")
    print("    list_recent_runs     → add ?limit=N&offset=K to /flows/{f}/runs")
    print("    get_run_status       → add /flows/{f}/runs/{r}/status endpoint")
    print("    find_failed_tasks    → add /flows/{f}/runs/{r}/failed_tasks")
    print("    time_filtered_runs   → add ?created_after=<iso8601> predicate")
    print("    collect_run_artifacts→ add /flows/{f}/runs/{r}/artifacts?name=X")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python metaflow_agent_audit.py <FlowName>")
        print("       python metaflow_agent_audit.py HelloFlow")
        print("       python metaflow_agent_audit.py AuditFlow")
        sys.exit(1)
    run_audit(sys.argv[1])
