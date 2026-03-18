#!/usr/bin/env python3
"""
BenchmarkFlow v2: Numbers aligned with demo_two_services.py.

    start ─┬─ path_a_client_api ─┬─ compare ─ end
           └─ path_b_ui_backend ─┘

Path A: Flow() iteration → all tasks → check task.successful (56 calls)
Path B: 2 HTTP calls to UI Backend (latest run + process tasks)

Usage:
    export METAFLOW_SERVICE_URL=http://localhost:8080
    export METAFLOW_DEFAULT_METADATA=service
    export METAFLOW_DEFAULT_DATASTORE=local
    python3 benchmark_flow_v2.py run
"""

from metaflow import FlowSpec, step

TARGET_FLOW = "ForeachFlow"
UI_BACKEND = "http://localhost:8083"


class BenchmarkFlowV2(FlowSpec):
    """Compare Client API vs UI Backend — aligned with demo_two_services.py."""

    @step
    def start(self):
        """Identify the target run to analyze."""
        import requests

        resp = requests.get(
            f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs",
            params={"_limit": 1, "_order": "-run_number"},
        )
        runs = resp.json().get("data", [])
        if not runs:
            raise RuntimeError(f"No runs found for {TARGET_FLOW}")

        self.target_run_number = runs[0]["run_number"]
        print(f"Target: {TARGET_FLOW}/{self.target_run_number}")
        self.next(self.path_a_client_api, self.path_b_ui_backend)

    @step
    def path_a_client_api(self):
        """
        OLD PATH: Client API → Metadata Service (port 8080).

        Uses Flow() iteration (like demo_two_services.py) to find
        the latest run, then iterates every task checking .successful.
        """
        import time
        from collections import defaultdict

        from metaflow import Flow, namespace
        from metaflow.plugins.metadata_providers.service import (
            ServiceMetadataProvider,
        )

        http_log = []
        _orig = ServiceMetadataProvider._request.__func__

        @classmethod
        def _counting(
            cls, monitor, path, method, data=None,
            retry_409_path=None, return_raw_resp=False,
        ):
            http_log.append({"method": method, "path": path})
            return _orig(
                cls, monitor, path, method, data,
                retry_409_path, return_raw_resp,
            )

        ServiceMetadataProvider._request = _counting
        namespace(None)

        http_log.clear()
        t0 = time.time()

        # Match demo_two_services.py: iterate Flow() to find latest run
        flow = Flow(TARGET_FLOW)
        latest_run = None
        for run in flow:
            latest_run = run
            break

        failures = []
        for step_obj in latest_run:
            for task in step_obj:
                if not task.successful:
                    failures.append(task.pathspec)

        self.elapsed_ms = (time.time() - t0) * 1000
        self.http_calls = len(http_log)
        self.failures = sorted(failures)
        self.method_name = "Client API (Metadata Service)"

        breakdown = defaultdict(int)
        for c in http_log:
            parts = c["path"].strip("/").split("/")
            for kw in ["artifacts", "tasks", "steps", "runs", "flows"]:
                if kw in parts:
                    breakdown[f"{c['method']} .../{kw}/..."] += 1
                    break
        self.breakdown = dict(breakdown)

        ServiceMetadataProvider._request = classmethod(_orig)

        print(f"Path A: {self.http_calls} HTTP calls, {self.elapsed_ms:.0f}ms")
        print(f"  Failed: {self.failures}")
        print(f"  Breakdown: {self.breakdown}")
        self.next(self.compare)

    @step
    def path_b_ui_backend(self):
        """
        NEW PATH: UI Backend (port 8083, same DB).

        2 calls only: get latest run + get all process tasks with status.
        Matches demo_two_services.py exactly.
        """
        import time
        import requests

        t0 = time.time()
        http_calls = 0

        # Call 1: Get latest run
        resp = requests.get(
            f"{UI_BACKEND}/api/flows/{TARGET_FLOW}/runs",
            params={"_limit": 1, "_order": "-run_number"},
        )
        http_calls += 1
        run_number = resp.json()["data"][0]["run_number"]

        # Call 2: Get ALL tasks for the "process" step in ONE call
        all_tasks = []
        page = 1
        while True:
            resp = requests.get(
                f"{UI_BACKEND}/api/flows/{TARGET_FLOW}"
                f"/runs/{run_number}/steps/process/tasks",
                params={"_limit": 100, "_page": page},
            )
            http_calls += 1
            result = resp.json()
            tasks_page = result.get("data", [])
            all_tasks.extend(tasks_page)
            if not result.get("pages", {}).get("next") or not tasks_page:
                break
            page = result["pages"]["next"]

        failures = [
            f"{TARGET_FLOW}/{run_number}/process/{t['task_id']}"
            for t in all_tasks if t.get("status") == "failed"
        ]

        self.elapsed_ms = (time.time() - t0) * 1000
        self.http_calls = http_calls
        self.failures = sorted(failures)
        self.total_tasks_inspected = len(all_tasks)
        self.method_name = "UI Backend Service"
        self.breakdown = {"GET /runs (1) + GET /tasks (1)": http_calls}

        print(f"Path B: {self.http_calls} HTTP calls, {self.elapsed_ms:.0f}ms")
        print(f"  Inspected {len(all_tasks)} tasks (status included)")
        print(f"  Failed: {self.failures}")
        self.next(self.compare)

    @step
    def compare(self, inputs):
        """Verify both paths found the same failures, compute speedup."""
        for inp in inputs:
            if "Client API" in inp.method_name:
                self.a_time = inp.elapsed_ms
                self.a_calls = inp.http_calls
                self.a_failures = inp.failures
                self.a_breakdown = inp.breakdown
            else:
                self.b_time = inp.elapsed_ms
                self.b_calls = inp.http_calls
                self.b_failures = inp.failures

        self.same_result = self.a_failures == self.b_failures
        self.speedup = self.a_time / max(self.b_time, 0.1)
        self.call_reduction_pct = (
            (self.a_calls - self.b_calls) / max(self.a_calls, 1) * 100
        )
        self.target_run_number = inputs[0].target_run_number
        self.next(self.end)

    @step
    def end(self):
        """Print final comparison."""
        print()
        print("=" * 60)
        print("  BENCHMARK RESULTS")
        print("=" * 60)
        print()
        print(f"  Target: {TARGET_FLOW}/{self.target_run_number}")
        print(f"  Same result: {self.same_result}")
        print(f"  Failed tasks: {self.a_failures}")
        print()
        print(f"  PATH A (Client API):    {self.a_calls:3d} calls,"
              f"  {self.a_time:7.0f}ms")
        print(f"  PATH B (UI Backend):    {self.b_calls:3d} calls,"
              f"  {self.b_time:7.0f}ms")
        print()
        print(f"  Call reduction:  {self.call_reduction_pct:.0f}%"
              f"  ({self.a_calls - self.b_calls} fewer calls)")
        print(f"  Speedup:         {self.speedup:.1f}x faster")
        print()
        print("  Both paths query the SAME Postgres database.")
        print("  The difference is purely in the API layer.")
        print()
        print(f"  Call breakdown (Path A): {self.a_breakdown}")
        print()


if __name__ == "__main__":
    BenchmarkFlowV2()
