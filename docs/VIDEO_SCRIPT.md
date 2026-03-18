# YouTube Demo Video Script — 60 seconds (with voice)

## Setup Before Recording

**Screen layout:** Two panes side by side
- Left: terminal (large font, 18pt+, dark theme)
- Right: browser with Metaflow UI at `http://localhost:3000`

**Pre-run:** Services up, ForeachFlow data loaded, UI open on the flows page.

---

## The Script

### [0:00–0:10] Hook

**Show:** Terminal, cursor blinking. You've already typed the command but haven't hit enter.

**Say:**
> "Imagine an AI agent is debugging your Metaflow pipeline. It asks one question: which tasks failed? The flow has 50 parallel tasks. Let's see what happens."

**Hit enter** on `python3 benchmark_three_paths.py run`

---

### [0:10–0:25] The Race

**Show:** Output streaming live. Three branches running in parallel.

**Say** (as output appears):
> "This is a Metaflow flow that benchmarks itself. Three parallel branches, each answering the same question a different way."

*path_b finishes*
> "The UI Backend finishes first — 2 calls, 35 milliseconds."

*path_c finishes*
> "The smart metadata path — 4 calls, 350 milliseconds. No UI Backend needed."

*path_a still running...*
> "And the naive path is still going... fetching one artifact per task..."

*path_a finishes*
> "56 HTTP calls. Almost 4 seconds. To get 50 booleans."

---

### [0:25–0:40] The Results

**Show:** The final comparison table appears on screen.

**Say:**
> "All three paths found the same failed task. Same database, same answer. But the smart path used 4 calls instead of 56 — almost 9 times faster. And it works on every Metaflow deployment, no extra services needed."

**Pause** on the table for 2-3 seconds so viewer can read.

---

### [0:40–0:52] The DAG

**Switch to browser.** Click into `BenchmarkThreePaths`, latest run, DAG tab.

**Say:**
> "Here it is in the Metaflow UI. You can see the three branches — the naive path took 3 seconds, the smart path took 350 milliseconds. Same fork, same join, same result. The difference is purely in how we query the metadata."

**Click on path_a, then path_c** to show the duration in the UI.

---

### [0:52–1:00] The Punch Line

**Switch back to terminal** or show a clean end frame.

**Say:**
> "The trick? The metadata service already stores task success as a field called attempt_ok. There's already an endpoint to query it — filter_tasks_by_metadata — it's just not used for this. No new infrastructure, just smarter use of what's already there. That's what this GSoC project builds."

---

## Recording Tips

1. **Large terminal font** (18pt+), readable at 1080p on a phone screen
2. **Speak naturally**, not fast — you have 60 seconds, that's plenty for these few sentences
3. **Speed up** the Metaflow startup output (the "Validating your flow..." part) at 2x in your editor
4. **Don't rush the results table** — give the viewer 3 seconds to read it
5. **Record at 1920x1080** minimum
6. **Practice once** before recording — the flow takes ~8 seconds to run, so you have natural pauses

## Commands to Run Before Recording

```bash
cd /Users/landigf/Desktop/Code/GSoC/demo
source /Users/landigf/Desktop/Code/GSoC/venv/bin/activate
export METAFLOW_SERVICE_URL=http://localhost:8080
export METAFLOW_DEFAULT_METADATA=service
export METAFLOW_DEFAULT_DATASTORE=local

# Verify services
curl -s http://localhost:8080/flows | python3 -c "import json,sys; print(len(json.load(sys.stdin)), 'flows')"
curl -s http://localhost:8083/api/flows | python3 -c "import json,sys; print(len(json.load(sys.stdin)['data']), 'flows')"

# Open Metaflow UI
open http://localhost:3000

# Pre-type the command so you just hit enter on camera:
# python3 benchmark_three_paths.py run
```
