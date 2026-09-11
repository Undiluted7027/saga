# Saga POC evaluation

This document records the integration checks and first dogfooding pass for
ticket #7. It records what Saga did and where it fell short.

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
median_ms: 34.352
p95_ms: 41.204
python: 3.12.13
platform: macOS-26.6.1-arm64-arm-64bit
```

The acceptance threshold is a median and p95 below one second.

## Why developer evaluation is deferred

The first dogfooding pass found that Saga still asks too much of the reader. Return summaries turn into inventories. Important boundaries remain noisy on ordinary functions. A call to a helper in the same file can stop the useful part of the analysis.

Outside sessions would mostly confirm problems we already know about. Saga needs to answer these questions on non-curated code first.

## Dogfooding during the revision

Use functions from Saga and other Python code, not examples written for the demo. Start with one question: what affects this return, what state may change, or which exception can escape? Compare Saga's answer with the code and record anything missing, misleading, or distracting.

External evaluation can start when the card answers those questions outside the fixture directory without burying the answer under unresolved boundaries.

## Later developer evaluation protocol

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
Decision: revise
Evidence summary: The analyzer and editor flow work. The known slicing and
panel regressions are fixed. Return presentation remains unwieldy, boundaries
are still noisy, and calls to local helpers end useful analysis too early.
Before external evaluation: Build the capability slices in docs/poc.md and
dogfood them outside the fixture directory. Start developer sessions when the
card can answer useful questions without fixture-specific help.
```
