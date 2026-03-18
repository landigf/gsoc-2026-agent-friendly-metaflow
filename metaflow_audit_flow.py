"""
Multi-step flow used to generate data for the agent audit.
More steps = more visible N+1 problem in the audit results.

Usage:
    python metaflow_audit_flow.py run
    python metaflow_audit_flow.py run  # run 3+ times for richer audit data
"""
from metaflow import FlowSpec, step, Parameter


class AuditFlow(FlowSpec):
    """Five-step linear flow simulating a realistic ML pipeline."""

    n_items = Parameter("n_items", default=4, type=int)

    @step
    def start(self):
        self.items = list(range(self.n_items))
        self.next(self.preprocess)

    @step
    def preprocess(self):
        self.processed = [x * 2 for x in self.items]
        self.next(self.train)

    @step
    def train(self):
        self.model_weights = [w / 10.0 for w in self.processed]
        self.next(self.evaluate)

    @step
    def evaluate(self):
        self.score = sum(self.model_weights) / len(self.model_weights)
        print(f"Model score: {self.score:.3f}")
        self.next(self.end)

    @step
    def end(self):
        print(f"Pipeline complete. Score={self.score:.3f}")


if __name__ == "__main__":
    AuditFlow()
