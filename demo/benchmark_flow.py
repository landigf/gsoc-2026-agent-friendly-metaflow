#!/usr/bin/env python3
"""
BenchmarkFlow: Metaflow benchmarking itself.

Runs both approaches as parallel branches so you can visualize
them side-by-side in the Metaflow UI:

    start ─┬─ path_a_client_api ─┬─ compare ─ end
           └─ path_b_ui_backend ─┘

Question answered: "Which tasks failed in the latest ForeachFlow run?"

Path A: Standard Client API → Metadata Service (port 8080)
        Iterates every task, fetches _success artifact per task.

Path B: Direct HTTP → UI Backend Service (port 8083)
        1-2 calls, status included in response.

Both hit the SAME Postgres database.

Usage:
    export METAFLOW_SERVICE_URL=http://localhost:8080
    export METAFLOW_DEFAULT_METADATA=service
    export METAFLOW_DEFAULT_DATASTORE=local
    python3 benchmark_flow.py run
"""

from metaflow import FlowSpec, step

TARGET_FLOW = "ForeachFlow"
UI_BACKEND = "http://localhost:8083"


class BenchmarkFlow(FlowSpec):
    """
    Compare two paths to find failed tasks in a ForeachFlow run.
    Visualize in Metaflow UI to see the time difference directly.
    """

    @step
    def start(self):
        """Identify the target run to analyze."""
        import requests

        # Use the UI Backend to find the latest ForeachFlow run number
        # (both paths will query the same run for a fair comparison)
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

    # ── Path A: the slow way (what exists today) ─────────────────

    @step
    def path_a_client_api(self):
        """
        OLD PATH: Client API -> Metadata Service (port 8080).

        Iterates every step and task, fetches the _success
        artifact for each task individually. O(tasks) HTTP calls.
        """
        import time
        from collections import defaultdict

        from metaflow import Flow, Run, namespace
        from metaflow.plugins.metadata_providers.service import (
            ServiceMetadataProvider,
        )

        # ── instrument _request to count HTTP calls ──
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

        # ── benchmark ──
        http_log.clear()
        t0 = time.time()

        run = Run(f"{TARGET_FLOW}/{self.target_run_number}")
        failures = []
        for step_obj in run:
            for task in step_obj:
                if not task.successful:
                    failures.append(task.pathspec)

        self.elapsed_ms = (time.time() - t0) * 1000
        self.http_calls = len(http_log)
        self.failures = sorted(failures)
        self.method_name = "Client API (Metadata Service)"

        # breakdown by object type
        breakdown = defaultdict(int)
        for c in http_log:
            parts = c["path"].strip("/").split("/")
            for kw in ["artifacts", "tasks", "steps", "runs", "flows"]:
                if kw in parts:
                    breakdown[f"{c['method']} .../{kw}/..."] += 1
                    break
        self.breakdown = dict(breakdown)

        # restore
        ServiceMetadataProvider._request = classmethod(_orig)

        print(f"Path A: {self.http_calls} HTTP calls, {self.elapsed_ms:.0f}ms")
        print(f"  Failed: {self.failures}")
        print(f"  Breakdown: {self.breakdown}")

        self.next(self.compare)

    # ── Path B: the fast way (what GSoC will enable) ─────────────

    @step
    def path_b_ui_backend(self):
        """
        NEW PATH: UI Backend Service (port 8083, same DB).

        Single paginated query returns all tasks WITH status.
        No per-task artifact fetching needed.
        """
        import time
        import requests

        t0 = time.time()
        http_calls = 0

        # Get ALL tasks for every step in one pass
        # First get the steps
        resp = requests.get(
            f"{UI_BACKEND}/api/flows/{TARGET_FLOW}"
            f"/runs/{self.target_run_number}/steps",
        )
        http_calls += 1
        steps = resp.json().get("data", [])

        failures = []
        total_tasks = 0

        for s in steps:
            step_name = s["step_name"]
            page = 1
            while True:
                resp = requests.get(
                    f"{UI_BACKEND}/api/flows/{TARGET_FLOW}"
                    f"/runs/{self.target_run_number}"
                    f"/steps/{step_name}/tasks",
                    params={"_limit": 100, "_page": page},
                )
                http_calls += 1
                result = resp.json()
                tasks_page = result.get("data", [])
                total_tasks += len(tasks_page)

                for t in tasks_page:
                    if t.get("status") == "failed":
                        pathspec = (
                            f"{TARGET_FLOW}/{self.target_run_number}"
                            f"/{step_name}/{t['task_id']}"
                        )
                        failures.append(pathspec)

                next_page = result.get("pages", {}).get("next")
                if next_page is None or not tasks_page:
                    break
                page = next_page

        self.elapsed_ms = (time.time() - t0) * 1000
        self.http_calls = http_calls
        self.failures = sorted(failures)
        self.total_tasks_inspected = total_tasks
        self.method_name = "UI Backend Service"
        self.breakdown = {f"GET (across {len(steps)} steps)": http_calls}

        print(f"Path B: {self.http_calls} HTTP calls, {self.elapsed_ms:.0f}ms")
        print(f"  Inspected {total_tasks} tasks across {len(steps)} steps")
        print(f"  Failed: {self.failures}")

        self.next(self.compare)

    # ── Join: verify same answer, compute speedup ────────────────

    @step
    def compare(self, inputs):
        """
        Verify both paths found the same failures,
        then compute the speedup.
        """
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
        # carry forward from either branch (same value)
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
    BenchmarkFlow()
