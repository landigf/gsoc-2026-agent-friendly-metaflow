#!/usr/bin/env python3
"""
DEMO: Two paths to the same answer.

Question: "Which tasks failed in the latest ForeachFlow run?"

Path A: Standard Client API -> Metadata Service (port 8080)
        Iterates every task, checks success by fetching artifacts
        from the metadata service + datastore — many HTTP calls.

Path B: Direct HTTP to UI Backend Service (port 8083)
        Single query with status included in response — 1-2 HTTP calls.

Both services query the SAME Postgres database. The difference
is purely in the API layer.

Usage:
    export METAFLOW_SERVICE_URL=http://localhost:8080
    export METAFLOW_DEFAULT_METADATA=service
    export METAFLOW_DEFAULT_DATASTORE=local
    python3 demo_two_services.py
"""
import os
import sys
import time
import requests
from collections import defaultdict

# Ensure metadata service is configured
os.environ.setdefault("METAFLOW_SERVICE_URL", "http://localhost:8080")
os.environ.setdefault("METAFLOW_DEFAULT_METADATA", "service")
os.environ.setdefault("METAFLOW_DEFAULT_DATASTORE", "local")

from metaflow import Flow, namespace
from metaflow.plugins.metadata_providers.service import ServiceMetadataProvider

# ── Instrumentation: monkeypatch _request to count HTTP calls ──
http_log = []
_orig_request = ServiceMetadataProvider._request.__func__


@classmethod
def _counting_request(cls, monitor, path, method, data=None,
                      retry_409_path=None, return_raw_resp=False):
    http_log.append({"method": method, "path": path})
    return _orig_request(cls, monitor, path, method, data,
                         retry_409_path, return_raw_resp)


ServiceMetadataProvider._request = _counting_request
namespace(None)

FLOW_NAME = "ForeachFlow"
UI_BACKEND = "http://localhost:8083"

# ── Path A: Client API (what agents use today) ──
print("=" * 64)
print("PATH A: Standard Client API -> Metadata Service (port 8080)")
print("=" * 64)

http_log.clear()
t0 = time.time()

failures_a = []
flow = Flow(FLOW_NAME)
latest_run = None
for run in flow:
    latest_run = run
    break

if latest_run is None:
    print("  No runs found for", FLOW_NAME)
    sys.exit(1)

run_id = latest_run.pathspec.split("/")[-1]
print(f"  Inspecting latest run: {latest_run.pathspec}")

for step in latest_run:
    for task in step:
        if not task.successful:
            failures_a.append(task.pathspec)

time_a = time.time() - t0
calls_a = len(http_log)

breakdown_a = defaultdict(int)
for c in http_log:
    # Extract object type from path like /flows/X/runs/Y/steps/Z/tasks/W/...
    parts = c["path"].strip("/").split("/")
    # classify by last object type
    label = c["method"]
    for keyword in ["artifacts", "tasks", "steps", "runs", "flows"]:
        if keyword in parts:
            label = f"{c['method']} .../{keyword}/..."
            break
    breakdown_a[label] += 1

print(f"  Found {len(failures_a)} failed tasks: {failures_a}")
print(f"  HTTP calls to metadata service: {calls_a}")
print(f"  Time: {time_a*1000:.0f}ms")
print(f"  Breakdown:")
for k, v in sorted(breakdown_a.items()):
    print(f"    {k}: {v}")
print()

# ── Path B: Direct to UI Backend (port 8083) ──
print("=" * 64)
print("PATH B: UI Backend Service (port 8083, same Postgres DB)")
print("=" * 64)

t0 = time.time()
http_calls_b = 0

# 1. Get the latest run
resp = requests.get(
    f"{UI_BACKEND}/api/flows/{FLOW_NAME}/runs",
    params={"_limit": 1, "_order": "-run_number"}
)
http_calls_b += 1
runs_data = resp.json().get("data", [])
if not runs_data:
    print("  No runs found!")
    sys.exit(1)

run_number = runs_data[0]["run_number"]
run_status = runs_data[0].get("status", "unknown")
print(f"  Latest run: {FLOW_NAME}/{run_number} (status: {run_status})")

# 2. Get ALL tasks for the "process" step in ONE call
# (paginate if needed but typically 50 tasks fit in a couple pages)
all_tasks = []
page = 1
while True:
    resp = requests.get(
        f"{UI_BACKEND}/api/flows/{FLOW_NAME}/runs/{run_number}/steps/process/tasks",
        params={"_limit": 100, "_page": page}
    )
    http_calls_b += 1
    result = resp.json()
    tasks_page = result.get("data", [])
    all_tasks.extend(tasks_page)
    next_page = result.get("pages", {}).get("next")
    if next_page is None or not tasks_page:
        break
    page = next_page

# Filter for failed tasks CLIENT-SIDE (status is already in the response!)
failures_b = [
    f"{FLOW_NAME}/{run_number}/process/{t['task_id']}"
    for t in all_tasks if t.get("status") == "failed"
]

time_b = time.time() - t0

print(f"  Found {len(failures_b)} failed tasks: {failures_b}")
print(f"  HTTP calls to UI backend: {http_calls_b}")
print(f"  Total tasks inspected: {len(all_tasks)} (status included in response)")
print(f"  Time: {time_b*1000:.0f}ms")
print()

# ── Comparison ──
print("=" * 64)
print("COMPARISON")
print("=" * 64)
print(f"  Client API:  {calls_a:3d} HTTP calls, {time_a*1000:7.0f}ms")
print(f"  UI Backend:  {http_calls_b:3d} HTTP calls, {time_b*1000:7.0f}ms")
if calls_a > 0:
    reduction = (calls_a - http_calls_b) / calls_a * 100
    print(f"  Reduction:   {calls_a - http_calls_b} fewer calls ({reduction:.0f}%)")
if time_b > 0:
    speedup = time_a / time_b
    print(f"  Speedup:     {speedup:.1f}x faster")
print()
print("WHY THE DIFFERENCE?")
print("-" * 64)
print("  The Metadata Service (port 8080) is a thin REST API with")
print("  no query parameters — no filtering, no pagination, no status.")
print("  To check if a task failed, the Client API must:")
print(f"    1. GET each task's '_success' artifact ({len(all_tasks)} artifact lookups)")
print("    2. Read the artifact value from the local datastore")
print()
print("  The UI Backend (port 8083) queries the SAME Postgres DB but")
print("  returns task status directly in each record. One paginated")
print("  query returns everything needed.")
print()
print("  The GSoC project bridges this gap: making the Python Client")
print("  API leverage the UI Backend's rich query capabilities.")
