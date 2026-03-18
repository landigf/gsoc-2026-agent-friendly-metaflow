from metaflow import FlowSpec, step

class MultiStepFlow(FlowSpec):
    @step
    def start(self):
        self.data = "hello"
        self.next(self.transform)

    @step
    def transform(self):
        self.data = self.data.upper()
        self.next(self.validate)

    @step
    def validate(self):
        assert self.data == "HELLO"
        self.validated = True
        self.next(self.end)

    @step
    def end(self):
        print(f"Done. validated={self.validated}")

if __name__ == "__main__":
    MultiStepFlow()
