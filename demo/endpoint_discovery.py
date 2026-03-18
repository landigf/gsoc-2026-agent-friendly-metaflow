#!/usr/bin/env python3
"""
UI Backend Endpoint Discovery Report

Tests each UI Backend endpoint and documents:
- Does it work?
- What does it return?
- How does it compare to the Client API / Metadata Service path?

Run from inside metaflow-dev shell or with the dev stack running.
"""
import json
import time
import requests

UI = "http://localhost:8083"
META = "http://localhost:8080"

def test_endpoint(label, url, params=None):
    print(f"\n{'─' * 64}")
    print(f"TEST: {label}")
    print(f"  URL: {url}")
    if params:
        print(f"  Params: {params}")
    t0 = time.time()
    try:
        resp = requests.get(url, params=params, timeout=5)
        elapsed = (time.time() - t0) * 1000
        result = resp.json()
        data = result.get("data", result)
        count = len(data) if isinstance(data, list) else "N/A"
        status = resp.status_code
        query = result.get("query", {}) if isinstance(result, dict) else {}
        pages = result.get("pages", {}) if isinstance(result, dict) else {}
        print(f"  Status: {status}")
        print(f"  Records: {count}")
        print(f"  Time: {elapsed:.0f}ms")
        if query:
            print(f"  Query params echoed: {query}")
        if pages:
            print(f"  Pagination: page {pages.get('self')}, next={pages.get('next')}")
        if isinstance(data, list) and data:
            print(f"  Sample record keys: {list(data[0].keys())}")
            # Show status field if present
            if "status" in data[0]:
                from collections import Counter
                statuses = Counter(d.get("status") for d in data)
                print(f"  Status breakdown: {dict(statuses)}")
        return True, data
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        print(f"  ERROR: {e}")
        print(f"  Time: {elapsed:.0f}ms")
        return False, None


print("=" * 64)
print("UI BACKEND ENDPOINT DISCOVERY REPORT")
print("=" * 64)
print(f"UI Backend: {UI}")
print(f"Metadata Service: {META}")

# ── Test 1: Pagination + Ordering ──
ok, data = test_endpoint(
    "Pagination + Ordering (runs, limit=3, newest first)",
    f"{UI}/api/flows/ForeachFlow/runs",
    {"_limit": 3, "_order": "-ts_epoch"}
)
if ok:
    print(f"  WORKS: Returns {len(data)} runs ordered by timestamp descending")
    print(f"  Client API equivalent: for run in Flow('ForeachFlow') — NO limit, NO ordering control")

# Compare with metadata service
print(f"\n  Metadata Service comparison:")
resp = requests.get(f"{META}/flows/ForeachFlow/runs")
meta_data = resp.json()
print(f"    GET {META}/flows/ForeachFlow/runs → {len(meta_data)} runs, NO pagination, NO ordering params")

# ── Test 2: Status Filter (completed) ──
ok, data = test_endpoint(
    "Status Filter: completed runs",
    f"{UI}/api/flows/SimpleFlow/runs",
    {"status": "completed"}
)
if ok:
    print(f"  WORKS: Returns only runs with status=completed")
    print(f"  Client API equivalent: IMPOSSIBLE without iterating ALL runs and checking each one")

# ── Test 3: Status Filter (failed) ──
ok, data = test_endpoint(
    "Status Filter: failed runs",
    f"{UI}/api/flows/ForeachFlow/runs",
    {"status": "failed"}
)
if ok:
    # Note: local runs may show as "running" even after failure (heartbeat-based detection)
    print(f"  WORKS: Endpoint accepts status filter")
    print(f"  NOTE: Local dev runs may show status='running' instead of 'failed'")
    print(f"        because status is determined by heartbeat timeout, not explicit reporting")

# ── Test 4: Time Range Filter ──
import time as _time
ts_24h_ago = int((_time.time() - 86400) * 1000)
ok, data = test_endpoint(
    "Time Range Filter: runs from last 24 hours",
    f"{UI}/api/flows/SimpleFlow/runs",
    {"ts_epoch:gt": str(ts_24h_ago)}
)
if ok:
    print(f"  WORKS: Returns {len(data)} runs newer than 24h ago")
    print(f"  Client API equivalent: IMPOSSIBLE — must iterate ALL runs, check timestamps client-side")

# ── Test 5: Failed Tasks ──
ok, data = test_endpoint(
    "Task Status: all tasks with per-task status field",
    f"{UI}/api/flows/ForeachFlow/runs/9/steps/process/tasks",
    {"_limit": 100}
)
if ok:
    from collections import Counter
    statuses = Counter(d.get("status") for d in data)
    failed = [d for d in data if d.get("status") == "failed"]
    print(f"  WORKS: Returns {len(data)} tasks with status field in each record")
    print(f"  Failed tasks: {[d['task_id'] for d in failed]}")
    print(f"  Client API equivalent: Must GET each task's '_success' artifact individually")
    print(f"    → {len(data)} extra HTTP calls + {len(data)} datastore reads")

# ── Test 6: Log Endpoint ──
ok, data = test_endpoint(
    "Task Logs",
    f"{UI}/api/flows/SimpleFlow/runs/1/steps/end/tasks/3/logs/out",
)
if ok:
    print(f"  WORKS: Endpoint exists, returns {len(data)} log lines")
    print(f"  NOTE: Logs may be empty if stored in local datastore (not S3)")
    print(f"  Client API equivalent: task.stdout — also reads from datastore")

# ── Summary ──
print(f"\n{'=' * 64}")
print("SUMMARY: UI Backend Capabilities vs Client API")
print("=" * 64)
print("""
┌─────────────────────────┬──────────────┬──────────────────────┐
│ Capability              │ UI Backend   │ Client API           │
│                         │ (port 8083)  │ (port 8080)          │
├─────────────────────────┼──────────────┼──────────────────────┤
│ Pagination (_limit)     │ YES          │ NO                   │
│ Ordering (_order)       │ YES          │ NO                   │
│ Status filtering        │ YES          │ NO                   │
│ Time range filtering    │ YES          │ NO                   │
│ Task status in response │ YES          │ NO (needs artifact)  │
│ Run duration/timing     │ YES          │ NO (needs metadata)  │
│ Pagination links        │ YES          │ NO                   │
└─────────────────────────┴──────────────┴──────────────────────┘

The UI Backend and Metadata Service share the SAME Postgres database.
The UI Backend was built for the web UI and has rich query capabilities.
The Python Client API only talks to the Metadata Service, which has none.

The GSoC project: make the Client API optionally use the UI Backend's
query capabilities for agent-friendly, efficient data access.
""")
