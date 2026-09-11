# Saga POC evaluation

This document records the final integration checks for ticket #7. It is
deliberately a record of observed developer behavior, not an argument that the
analyzer is complete.

## Reproducible demo

From a clean checkout:

```sh
uv sync --extra test
npm test
uv run python -m unittest discover -s tests
uv run --extra test saga test fixture/process_order.py::process_order \
  --format terminal -- fixture/test_process_order.py
```

Open the repository in VS Code through the Extension Development Host. In
`fixture/process_order.py`, place the cursor on `process_order`, open the Saga
evidence card, run its tests, inspect the separate derived and observed
sections, and follow source links. Edit and save the function; the open static
card should refresh. `fixture/partial_analysis.py` demonstrates a return claim
surviving beside an unsupported context-manager diagnostic, while
`unsupported_async` demonstrates an unsupported target.

## Static refresh latency

The benchmark includes the Python process startup and JSON card generation used
by the editor's static request. It warms up once, then measures 20 fresh CLI
requests and reports milliseconds:

```sh
uv run python scripts/benchmark_inspect.py \
  fixture/process_order.py::process_order
```

Recorded on 2026-09-11 from the integration checkout:

```text
measured_runs: 20
median_ms: 34.290
p95_ms: 39.127
python: 3.12.13
platform: macOS-26.6.1-arm64-arm-64bit
```

The acceptance threshold is a median and p95 below one second.

## Developer evaluation protocol

Run with at least five developers who did not write Saga. Use two comparable
unfamiliar functions or task variants so the card and no-card conditions are
not always experienced in the same order.

For each session, record:

- the task variant and whether Saga was available;
- time to the answer and whether the answer was correct;
- claims opened and source links followed;
- misleading, missing, or distracting information;
- a short qualitative explanation of what helped or hurt.

Give each developer the same code-understanding questions in both conditions,
counterbalancing which variant gets the card first. Do not coach them on which
claims to trust. Remove the card between conditions and keep the task wording
fixed.

## Decision

```text
Decision: pending developer sessions
Evidence summary: The automated demo and latency checks are complete; human
evaluation data is still required.
Changes required before further work: Run the five developer sessions and
replace this decision with `go`, `revise`, or `stop`.
```
