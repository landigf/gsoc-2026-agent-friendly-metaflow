from metaflow import FlowSpec, step

class SimpleFlow(FlowSpec):
    @step
    def start(self):
        self.value = 42
        self.next(self.end)

    @step
    def end(self):
        print(f"Done. value={self.value}")

if __name__ == "__main__":
    SimpleFlow()
