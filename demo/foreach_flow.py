from metaflow import FlowSpec, step

class ForeachFlow(FlowSpec):
    @step
    def start(self):
        self.items = list(range(50))
        self.next(self.process, foreach="items")

    @step
    def process(self):
        self.item = self.input
        if self.item == 47:
            raise Exception(f"Task {self.item} intentionally failed!")
        self.result = self.item * 2
        self.next(self.join)

    @step
    def join(self, inputs):
        self.results = [inp.result for inp in inputs if hasattr(inp, "result")]
        self.next(self.end)

    @step
    def end(self):
        print(f"Done. {len(self.results)} results collected.")

if __name__ == "__main__":
    ForeachFlow()
