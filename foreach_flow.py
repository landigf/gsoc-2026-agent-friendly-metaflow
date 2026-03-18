# File: foreach_flow.py
# This is CRITICAL - foreach creates many parallel tasks, which is where the amplification hurts
from metaflow import FlowSpec, step
import random

class ForeachFlow(FlowSpec):
    @step
    def start(self):
        self.items = list(range(50))  # 50 parallel tasks
        self.next(self.process, foreach='items')

    @step
    def process(self):
        self.result = self.input * 2
        # Intentionally fail some tasks to test failure detection
        if self.input == 7:
            raise Exception("Intentional failure for testing")
        self.next(self.join)

    @step
    def join(self, inputs):
        self.results = [inp.result for inp in inputs]
        self.next(self.end)

    @step
    def end(self):
        print(f"Processed {len(self.results)} items")

if __name__ == '__main__':
    ForeachFlow()
