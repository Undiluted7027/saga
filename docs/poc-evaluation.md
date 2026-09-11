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

### Readable claims (#13)

Dogfooded on 2026-09-11 against `_compact_domain` in `saga/render.py`, `_resolve` in `saga/effects.py`, and `describe_condition` in `saga/presentation.py`.

The card now names the expression returned on each path and states the conditions needed to reach it. Guards describe truthiness and simple comparisons instead of dumping the serialized condition. The CLI and editor use the same claim text, and the detail view keeps the source expression, analysis method, assumptions, source links, and limiting boundaries.

This made multi-return functions easier to follow. It also made the next problems harder to ignore. At that point, routine calls still created long boundary lists, recursive and module-local helpers remained opaque, and a large dependency slice was still dense when expanded. Those belonged to #12 and #11. They were not wording problems.

### One-hop module-local calls (#11)

Dogfooded on 2026-09-11 against `evaluate_observations` in `saga/observations.py`, `terminal` in `saga/render.py`, and `_resolve` in `saga/effects.py`.

Saga now follows a direct call to one supported synchronous function in the same module. The propagated claim records the caller, callee, call site, callee span, and simple argument bindings. The old unresolved boundary disappears. Recursion and calls made by the callee stop at a named `local_call_limit` boundary.

This removed misleading boundaries around `_number`, `_fingerprint`, and `_domain` while inspecting `evaluate_observations`. It also exposed a cost. Calling `_domain` twice produces two copies of its facts because the call sites are different, and each copy carries the callee's unresolved boundaries. That is honest but noisy. Ticket #12 should group this material without erasing either call site.

### Escaping explicit exceptions (#10)

Dogfooded on 2026-09-11 against `_selector` and `run_tests` in
`saga/testing.py`, `_rmtree_safe_fd` in Python 3.12's `shutil.py`, and
`_url_handler` in Python 3.12's `pydoc.py`.

`_selector` now reports the `ValueError` that can escape its entry guard.
`run_tests` carries that exception through the local `_selector` call and shows
the call site and argument binding. `_rmtree_safe_fd` contains an explicit
`OSError` caught by a local `except OSError`; Saga does not report it as
escaping. `_url_handler` contains both a handled `ValueError` and a separate
escaping `TypeError`; only the latter appears.

Saga now walks explicit raises outside the entry-guard prefix, removes raises
caught by a clear builtin handler, reports typed and unknown re-raises, and
propagates exceptions through one supported local call. It also marks cases it
cannot settle: custom handler types, rebound builtin exception names, and
context managers that may suppress an exception. Unresolved calls still do not
grow invented exception lists.

This slice answers “which explicit exception can escape?” on real functions.
It does not answer “can this function raise at all?” because implicit Python
errors and third-party call behavior remain outside the model.

### Boundary grouping (#12)

Dogfooded on 2026-09-11 against `evaluate_observations` in
`saga/observations.py` and `terminal` in `saga/render.py`.

The structured card still contains every boundary occurrence and every original
boundary ID. The CLI and editor now group occurrences only when the boundary
kind, target, and stop reason match. A group states the reason once, reports its
site count, and keeps every location and local call chain available. Different
failure reasons remain separate.

The default CLI view for `terminal` reduced 99 raw boundary sites to five
prominent group summaries plus one collapsed routine summary. Its 31
`lines.append(...)` sites no longer occupy 31 lines unless the developer asks
for `--show-boundary-sites`. The editor uses the same groups and puts repeated
locations behind an expandable section.

Routine calls remain available behind `--show-routine-boundaries`. Calls that
may mutate an unknown receiver, including `append`, `add`, `update`, and `pop`,
are no longer classified as routine. Module-local stop conditions, dynamic
behavior, unresolved external calls, and exception-flow limits have distinct
labels.

Grouping fixes repetition, not every form of noise. `evaluate_observations`
still has several unique prominent boundaries. Those are separate analysis
gaps rather than copies of the same warning, so this slice leaves them visible.

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
Evidence summary: The analyzer and editor flow work. Claims are readable and
Saga can follow one module-local call, report escaping explicit exceptions, and
group repeated boundaries without deleting their evidence. Some functions still
have many distinct analysis gaps, and the card has no question-focused views.
Before external evaluation: Finish the remaining capability slices in
docs/poc.md and dogfood them outside the fixture directory. Start developer
sessions when the card can answer useful questions without fixture-specific
help.
```
