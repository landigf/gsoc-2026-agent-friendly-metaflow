"""
Instrument the Metaflow Client API to count HTTP requests per logical operation.
This is the core audit tool.
"""
import functools
import json
from collections import defaultdict
from metaflow import Flow, Run, Step, Task, namespace

# For LOCAL metadata provider, we instrument differently
# For SERVICE metadata provider, we'd patch _request
# Let's start with local and count get_object calls

from metaflow.metadata_provider.metadata import MetadataProvider

call_log = []
original_get_object = MetadataProvider.get_object.__func__

@classmethod
def instrumented_get_object(cls, obj_type, sub_type, filters, attempt, *args):
    call_log.append({
        "obj_type": obj_type,
        "sub_type": sub_type,
        "filters": str(filters),
        "args": [str(a) for a in args],
    })
    return original_get_object(cls, obj_type, sub_type, filters, attempt, *args)

MetadataProvider.get_object = instrumented_get_object

def count_calls(operation_name, func):
    """Run a function and count how many get_object calls it triggers."""
    call_log.clear()
    result = func()
    print(f"\n{'='*60}")
    print(f"Operation: {operation_name}")
    print(f"Total get_object calls: {len(call_log)}")
    print(f"Breakdown:")
    type_counts = defaultdict(int)
    for call in call_log:
        key = f"{call['obj_type']}/{call['sub_type']}"
        type_counts[key] += 1
    for key, count in sorted(type_counts.items()):
        print(f"  {key}: {count}")
    print(f"{'='*60}")
    return result, len(call_log)

# Disable namespace filtering so we see everything
namespace(None)

# --- USE CASE 1: Check if a run was successful ---
print("\n\n=== USE CASE 1: Run.successful ===")
flow = Flow("SimpleFlow")
latest_run = None
for r in flow:
    latest_run = r
    break

if latest_run:
    run_id = latest_run.pathspec
    count_calls(
        f"Run('{run_id}').successful",
        lambda: Run(run_id).successful
    )

# --- USE CASE 2: Find the latest successful run ---
print("\n\n=== USE CASE 2: Flow.latest_successful_run ===")
count_calls(
    "Flow('SimpleFlow').latest_successful_run",
    lambda: Flow("SimpleFlow").latest_successful_run
)

# --- USE CASE 3: List all runs and check each one's status ---
print("\n\n=== USE CASE 3: Iterate runs and check .successful ===")
def check_all_runs():
    results = []
    for run in Flow("SimpleFlow"):
        results.append((run.id, run.successful))
    return results

count_calls("Check .successful on all SimpleFlow runs", check_all_runs)

# --- USE CASE 4: Find failed tasks in a foreach run ---
print("\n\n=== USE CASE 4: Find failed tasks in ForeachFlow ===")
def find_failed_tasks():
    failed = []
    for run in Flow("ForeachFlow"):
        for step in run:
            for task in step:
                if not task.successful:
                    failed.append(task.pathspec)
        break  # only check the latest run
    return failed

count_calls("Find failed tasks in latest ForeachFlow run", find_failed_tasks)

# --- USE CASE 5: Get artifact metadata without loading data ---
print("\n\n=== USE CASE 5: List artifacts for a task ===")
def list_artifacts():
    run = Flow("MultiStepFlow").latest_run
    end_task = run["end"].task
    return [(a.id, a.created_at) for a in end_task]

count_calls("List artifacts for end task", list_artifacts)

# --- USE CASE 6: Get task stdout ---
print("\n\n=== USE CASE 6: Task.stdout ===")
def get_stdout():
    run = Flow("SimpleFlow").latest_run
    end_task = run["end"].task
    return end_task.stdout[:100]  # even though we only want 100 chars...

count_calls("Get first 100 chars of stdout", get_stdout)

# --- USE CASE 7: Filter runs by time (simulate agent asking "runs from last 24h") ---
print("\n\n=== USE CASE 7: Time-filtered run listing ===")
from datetime import datetime, timedelta

def runs_last_24h():
    cutoff = datetime.now() - timedelta(hours=24)
    recent = []
    for run in Flow("SimpleFlow"):
        if run.created_at > cutoff:
            recent.append(run.id)
        # NOTE: we must iterate ALL runs because there's no server-side time filter
        # Even after finding old runs, we can't stop early because ordering isn't guaranteed
    return recent

count_calls("Find runs from last 24 hours", runs_last_24h)

# --- SUMMARY ---
print("\n\n" + "="*60)
print("AUDIT SUMMARY")
print("="*60)
print("Key findings to document:")
print("1. How many get_object calls per logical agent query?")
print("2. Which calls are to fetch data the agent doesn't need?")
print("3. Where is filtering done client-side vs server-side?")
print("4. What's the amplification factor for foreach-heavy flows?")
