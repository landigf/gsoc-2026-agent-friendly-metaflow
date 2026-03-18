#!/usr/bin/env python3
"""
Full Client API Efficiency Audit — All 6 GSoC Use Cases

Traces how the Client API translates to metadata service calls for each
use case listed in the GSoC proposal, comparing against what the UI Backend
can do with the same database.

Use cases:
  1. Listing recent runs filtered by success/failure status
  2. Listing runs/tasks filtered by time range
  3. Finding failed task(s) and retrieving error details
  4. Getting artifact metadata without loading artifact data
  5. Retrieving bounded/filtered log output
  6. Searching for artifacts across runs and tasks

Usage:
    export METAFLOW_SERVICE_URL=http://localhost:8080
    export METAFLOW_DEFAULT_METADATA=service
    export METAFLOW_DEFAULT_DATASTORE=local
    python3 full_audit.py
"""

import time
import sys
import json
from collections import defaultdict

import requests
from metaflow import Flow, Run, Step, Task, namespace
from metaflow.plugins.metadata_providers.service import ServiceMetadataProvider

# ── Configuration ──
METADATA_SERVICE = "http://localhost:8080"
UI_BACKEND = "http://localhost:8083"
TARGET_FLOW = "ForeachFlow"

# ── Instrumentation ──
_http_log = []
_orig_request = ServiceMetadataProvider._request.__func__

@classmethod
def _counting_request(cls, monitor, path, method, data=None,
                      retry_409_path=None, return_raw_resp=False):
    _http_log.append({"method": method, "path": path, "ts": time.time()})
    return _orig_request(cls, monitor, path, method, data,
                         retry_409_path, return_raw_resp)

def install_counter():
    _http_log.clear()
    ServiceMetadataProvider._request = _counting_request

def uninstall_counter():
    ServiceMetadataProvider._request = classmethod(_orig_request)

def measure(label, fn):
    """Run fn, return (result, calls, elapsed_ms, breakdown)."""
    install_counter()
    _http_log.clear()
    t0 = time.time()
    result = fn()
    elapsed = (time.time() - t0) * 1000
    calls = len(_http_log)
    breakdown = defaultdict(int)
    for c in _http_log:
        parts = c["path"].strip("/").split("/")
        for kw in ["artifacts", "tasks", "steps", "runs", "flows", "metadata"]:
            if kw in parts:
                breakdown[f"{c['method']} .../{kw}/..."] += 1
                break
    uninstall_counter()
    return result, calls, elapsed, dict(breakdown)

def measure_ui(label, fn):
    """Run fn that uses requests directly, measure time."""
    t0 = time.time()
    result, calls = fn()
    elapsed = (time.time() - t0) * 1000
    return result, calls, elapsed


# ── Use Case Implementations ──

def uc1_client_api():
    """UC1: List recent runs filtered by status (Client API)."""
    namespace(None)
    flow = Flow(TARGET_FLOW)
    failed_runs = []
    for run in flow:
        if not run.successful:
            failed_runs.append(run.id)
        if len(failed_runs) >= 5:
            break
    return failed_runs

def uc1_ui_backend():
    """UC1: List recent runs filtered by status (UI Backend)."""
    calls = 0
    resp = requests.get(f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs",
                        params={"_limit": 10, "_order": "-run_number"})
    calls += 1
    runs = resp.json().get("data", [])
    failed = [r["run_number"] for r in runs if r.get("status") == "failed"]
    return failed[:5], calls


def uc2_client_api():
    """UC2: List runs/tasks filtered by time range (Client API)."""
    namespace(None)
    flow = Flow(TARGET_FLOW)
    cutoff = time.time() - 86400  # last 24 hours
    recent_runs = []
    for run in flow:
        if run.created_at.timestamp() >= cutoff:
            recent_runs.append(run.id)
        else:
            break  # runs are ordered, so we can stop
    return recent_runs

def uc2_ui_backend():
    """UC2: List runs/tasks filtered by time range (UI Backend)."""
    calls = 0
    cutoff_ms = int((time.time() - 86400) * 1000)
    resp = requests.get(f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs",
                        params={"_order": "-ts_epoch",
                                "ts_epoch:gt": cutoff_ms})
    calls += 1
    runs = resp.json().get("data", [])
    return [r["run_number"] for r in runs], calls


def uc3_client_api():
    """UC3: Find failed tasks + error details (Client API)."""
    namespace(None)
    flow = Flow(TARGET_FLOW)
    latest_run = None
    for run in flow:
        latest_run = run
        break

    failures = []
    for step_obj in latest_run:
        for task in step_obj:
            if not task.successful:
                # Try to get the exception
                try:
                    exc = task["_exception"].data
                except (KeyError, Exception):
                    exc = "N/A"
                failures.append({
                    "pathspec": task.pathspec,
                    "exception": str(exc)[:200] if exc else "N/A",
                })
    return failures

def uc3_ui_backend():
    """UC3: Find failed tasks + error details (UI Backend)."""
    calls = 0
    # Get latest run
    resp = requests.get(f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs",
                        params={"_limit": 1, "_order": "-run_number"})
    calls += 1
    run_number = resp.json()["data"][0]["run_number"]

    # Get all steps
    resp = requests.get(f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs/{run_number}/steps")
    calls += 1
    steps = resp.json().get("data", [])

    failures = []
    for s in steps:
        resp = requests.get(
            f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs/{run_number}"
            f"/steps/{s['step_name']}/tasks",
            params={"_limit": 200})
        calls += 1
        for t in resp.json().get("data", []):
            if t.get("status") == "failed":
                failures.append({
                    "pathspec": f"{TARGET_FLOW}/{run_number}/{s['step_name']}/{t['task_id']}",
                    "status": t.get("status"),
                })
    return failures, calls


def uc4_client_api():
    """UC4: Get artifact metadata without loading data (Client API)."""
    namespace(None)
    flow = Flow(TARGET_FLOW)
    latest_run = None
    for run in flow:
        latest_run = run
        break

    # Get artifact names and metadata for the first step's first task
    artifacts_info = []
    for step_obj in latest_run:
        for task in step_obj:
            for art in task:
                artifacts_info.append({
                    "name": art.id,
                    "sha": art.sha,
                    "created_at": str(art.created_at),
                })
            break  # just first task
        break  # just first step
    return artifacts_info

def uc4_ui_backend():
    """UC4: Get artifact metadata without loading data (UI Backend)."""
    calls = 0
    # Get latest run
    resp = requests.get(f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs",
                        params={"_limit": 1, "_order": "-run_number"})
    calls += 1
    run_number = resp.json()["data"][0]["run_number"]

    # Get steps
    resp = requests.get(f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs/{run_number}/steps")
    calls += 1
    steps = resp.json().get("data", [])
    step_name = steps[0]["step_name"]

    # Get tasks for first step
    resp = requests.get(
        f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs/{run_number}"
        f"/steps/{step_name}/tasks",
        params={"_limit": 1})
    calls += 1
    task_id = resp.json()["data"][0]["task_id"]

    # Get artifacts for that task (metadata only, no data loading)
    resp = requests.get(
        f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs/{run_number}"
        f"/steps/{step_name}/tasks/{task_id}/artifacts",
        params={"_limit": 50})
    calls += 1
    artifacts = resp.json().get("data", [])
    return [{"name": a["name"], "ds_type": a.get("ds_type"),
             "type": a.get("type")} for a in artifacts], calls


def uc5_client_api():
    """UC5: Retrieve bounded/filtered log output (Client API)."""
    namespace(None)
    flow = Flow(TARGET_FLOW)
    latest_run = None
    for run in flow:
        latest_run = run
        break

    # Get stdout of the first task in the first step
    for step_obj in latest_run:
        for task in step_obj:
            try:
                full_log = task.stdout  # loads ENTIRE log as string
                last_lines = "\n".join(full_log.split("\n")[-10:]) if full_log else ""
                log_size = len(full_log) if full_log else 0
            except Exception:
                last_lines = ""
                log_size = 0
            return {"last_10_lines": last_lines[:200], "total_bytes": log_size}
    return {"last_10_lines": "", "total_bytes": 0}

def uc5_ui_backend():
    """UC5: Retrieve bounded/filtered log output (UI Backend)."""
    calls = 0
    # Get latest run
    resp = requests.get(f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs",
                        params={"_limit": 1, "_order": "-run_number"})
    calls += 1
    run_number = resp.json()["data"][0]["run_number"]

    # Get first step and task
    resp = requests.get(f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs/{run_number}/steps")
    calls += 1
    steps = resp.json().get("data", [])
    step_name = steps[0]["step_name"]

    resp = requests.get(
        f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs/{run_number}"
        f"/steps/{step_name}/tasks",
        params={"_limit": 1})
    calls += 1
    task_id = resp.json()["data"][0]["task_id"]

    # Get logs with limit (UI Backend supports _limit on log lines)
    resp = requests.get(
        f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs/{run_number}"
        f"/steps/{step_name}/tasks/{task_id}/logs/out",
        params={"_limit": 10, "_order": "-row"})
    calls += 1
    log_data = resp.json()
    return {"log_lines": len(log_data.get("data", [])),
            "status_code": resp.status_code}, calls


def uc6_client_api():
    """UC6: Search for artifacts across runs (Client API)."""
    namespace(None)
    flow = Flow(TARGET_FLOW)
    # Find all runs that have an artifact named "items" in the "start" step
    matching = []
    runs_checked = 0
    for run in flow:
        runs_checked += 1
        try:
            step = run["start"]
            for task in step:
                for art in task:
                    if art.id == "items":
                        matching.append({
                            "run": run.id,
                            "artifact": art.id,
                            "sha": art.sha,
                        })
                break  # first task only
        except (KeyError, StopIteration):
            pass
        if runs_checked >= 5:
            break
    return matching

def uc6_ui_backend():
    """UC6: Search for artifacts across runs (UI Backend)."""
    calls = 0
    # Get recent runs
    resp = requests.get(f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs",
                        params={"_limit": 5, "_order": "-run_number"})
    calls += 1
    runs = resp.json().get("data", [])

    matching = []
    for r in runs:
        # Get artifacts for start step
        resp = requests.get(
            f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs/{r['run_number']}"
            f"/steps/start/tasks",
            params={"_limit": 1})
        calls += 1
        tasks = resp.json().get("data", [])
        if tasks:
            task_id = tasks[0]["task_id"]
            resp = requests.get(
                f"{UI_BACKEND}/api/flows/{TARGET_FLOW}"
                f"/runs/{r['run_number']}/steps/start"
                f"/tasks/{task_id}/artifacts",
                params={"_limit": 50})
            calls += 1
            for a in resp.json().get("data", []):
                if a["name"] == "items":
                    matching.append({
                        "run": str(r["run_number"]),
                        "artifact": a["name"],
                        "ds_type": a.get("ds_type"),
                    })
    return matching, calls


# ── Main ──

def print_header():
    print()
    print("=" * 78)
    print("  FULL CLIENT API EFFICIENCY AUDIT")
    print("  GSoC 2026: Agent-Friendly Metaflow Client")
    print("=" * 78)
    print()
    print(f"  Target flow: {TARGET_FLOW}")
    print(f"  Metadata Service: {METADATA_SERVICE}")
    print(f"  UI Backend: {UI_BACKEND}")
    print()

def run_use_case(num, title, client_fn, ui_fn):
    print(f"  {'─' * 70}")
    print(f"  USE CASE {num}: {title}")
    print(f"  {'─' * 70}")
    print()

    # Client API path
    result_a, calls_a, time_a, breakdown_a = measure(f"UC{num} Client", client_fn)

    # UI Backend path
    result_b, calls_b, time_b = measure_ui(f"UC{num} UI Backend", ui_fn)

    speedup = time_a / max(time_b, 0.1)
    call_reduction = (calls_a - calls_b) / max(calls_a, 1) * 100

    print(f"    Client API:    {calls_a:4d} HTTP calls,  {time_a:7.0f}ms")
    print(f"    UI Backend:    {calls_b:4d} HTTP calls,  {time_b:7.0f}ms")
    print(f"    Reduction:     {call_reduction:.0f}% fewer calls "
          f"({calls_a - calls_b} saved)")
    print(f"    Speedup:       {speedup:.1f}x faster")
    print(f"    Breakdown (A): {breakdown_a}")
    print()

    return {
        "use_case": num,
        "title": title,
        "client_calls": calls_a,
        "client_ms": round(time_a),
        "ui_calls": calls_b,
        "ui_ms": round(time_b),
        "speedup": round(speedup, 1),
        "call_reduction_pct": round(call_reduction),
        "breakdown": breakdown_a,
    }


def main():
    # Verify services are up
    try:
        requests.get(f"{METADATA_SERVICE}/flows", timeout=3)
    except Exception:
        print(f"ERROR: Metadata service not reachable at {METADATA_SERVICE}")
        sys.exit(1)
    try:
        requests.get(f"{UI_BACKEND}/api/flows", timeout=3)
    except Exception:
        print(f"ERROR: UI Backend not reachable at {UI_BACKEND}")
        sys.exit(1)

    print_header()

    results = []

    results.append(run_use_case(
        1, "List recent runs filtered by status",
        uc1_client_api, uc1_ui_backend))

    results.append(run_use_case(
        2, "List runs filtered by time range",
        uc2_client_api, uc2_ui_backend))

    results.append(run_use_case(
        3, "Find failed tasks + error details",
        uc3_client_api, uc3_ui_backend))

    results.append(run_use_case(
        4, "Artifact metadata without loading data",
        uc4_client_api, uc4_ui_backend))

    results.append(run_use_case(
        5, "Bounded/filtered log output",
        uc5_client_api, uc5_ui_backend))

    results.append(run_use_case(
        6, "Cross-run artifact search",
        uc6_client_api, uc6_ui_backend))

    # Summary table
    print()
    print("  " + "=" * 70)
    print("  SUMMARY")
    print("  " + "=" * 70)
    print()
    print(f"  {'UC':<4} {'Title':<40} {'API calls':<12} {'UI calls':<12} {'Speedup':<10}")
    print(f"  {'──':<4} {'─' * 40} {'─' * 11} {'─' * 11} {'─' * 9}")
    total_api = 0
    total_ui = 0
    for r in results:
        total_api += r["client_calls"]
        total_ui += r["ui_calls"]
        print(f"  {r['use_case']:<4} {r['title'][:40]:<40} "
              f"{r['client_calls']:>8}     {r['ui_calls']:>8}     "
              f"{r['speedup']:>6.1f}x")
    print(f"  {'──':<4} {'─' * 40} {'─' * 11} {'─' * 11} {'─' * 9}")
    print(f"  {'SUM':<4} {'All 6 use cases':<40} "
          f"{total_api:>8}     {total_ui:>8}     "
          f"{total_api/max(total_ui,1):>6.1f}x")
    print()
    print(f"  Total HTTP calls saved: {total_api - total_ui}")
    print(f"  Overall call reduction: "
          f"{(total_api - total_ui) / max(total_api, 1) * 100:.0f}%")
    print()

    # Save results as JSON for later use
    with open("audit_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("  Results saved to audit_results.json")
    print()


if __name__ == "__main__":
    main()
