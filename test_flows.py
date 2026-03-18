# File: test_flows.py
from metaflow import FlowSpec, step, Parameter

class SimpleFlow(FlowSpec):
    @step
    def start(self):
        self.x = 42
        self.next(self.end)

    @step
    def end(self):
        print(f"Result: {self.x}")

if __name__ == '__main__':
    SimpleFlow()
