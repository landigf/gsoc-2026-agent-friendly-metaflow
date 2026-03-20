# Email to send

**To:** help@metaflow.org
**Subject:** [GSOC 2026 Proposal]: [Your Name] — Agent-Friendly Metaflow Client

---

Hi,

I'm [name], MSc CS at ETH Zurich. Applying for the Agent-Friendly Metaflow Client project (mentor: Valay Dave).

I pointed an LLM agent at a ForeachFlow and asked: "Which tasks failed in my latest run?" It wrote 6 lines of Client API code, ran them, and the answer came back correct — but it took 57 HTTP calls and 2 seconds. 51 of those calls are `GET .../artifacts/_success`, one per task, each hitting the datastore to unpickle a boolean. The full API trace is in the attached proposal.

The metadata service already has what it needs to fix this. `filter_tasks_by_metadata` (service >= 2.5.0) queries `attempt_ok` directly from the metadata table — same answer, 4 calls, no datastore reads. The DB layer supports LIMIT/ORDER BY via `find_records()` but the HTTP endpoints don't expose them.

I instrumented `ServiceMetadataProvider._request` across all six use cases from the project description, set up the dev stack on minikube, and built a Metaflow flow (BenchmarkThreePaths) that runs three approaches as parallel branches — visible in the Metaflow UI.

Attached:
- Full proposal (PDF)
- Presentation (21 slides)
- 60-second demo video: [YouTube link]
- LLM API trace (JSON) — the raw proof

I'm in #gsoc on Slack.

[Your name]
