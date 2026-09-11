# Saga: evidence-backed Python code intelligence

## Saga POC

Saga analyzes one supported Python 3.12 module-level function and returns a
versioned evidence card. Static claims and test observations are separate, and
every claim links back to source spans.

To run its focused tests:

```sh
npm test
uv run python -m unittest discover -s tests
```

To inspect the fixture target from the CLI:

```sh
uv run saga inspect fixture/process_order.py::process_order --format terminal
```

To add test-observed evidence, install/use the optional test extra and run the
tests through Saga:

```sh
uv sync --extra test
uv run --extra test saga test fixture/process_order.py::process_order \
  --format terminal -- fixture/test_process_order.py
```

The default trace is written to `.saga/trace.json`. It is bounded, redacts
sensitive fields, records the Python environment, and is an observation input;
it is not proof of behavior outside the recorded executions.

To try the editor flow, open this repository in VS Code, install the repository
as an extension through the Extension Development Host (`Run Extension`), and
open `fixture/process_order.py`. Place the cursor on `process_order`, then use
`Saga: Open Evidence Card`. In the card, choose `Run tests for this function`
to see derived and observed claims in separate sections. Source links navigate
back to the supporting code.
