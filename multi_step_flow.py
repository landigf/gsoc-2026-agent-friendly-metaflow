# File: multi_step_flow.py
from metaflow import FlowSpec, step

class MultiStepFlow(FlowSpec):
    @step
    def start(self):
        self.data = "initial"
        self.next(self.train)

    @step
    def train(self):
        self.model = f"model_from_{self.data}"
        self.next(self.evaluate)

    @step
    def evaluate(self):
        self.score = 0.95
        self.next(self.end)

    @step
    def end(self):
        print(f"Score: {self.score}")

if __name__ == '__main__':
    MultiStepFlow()
