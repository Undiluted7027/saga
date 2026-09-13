# Saga: evidence-backed Python code intelligence

## Saga POC

Saga analyzes one supported Python 3.12 module-level function and returns a
versioned evidence card. Static claims and test observations are separate, and
every claim links back to source spans.

Direct calls to supported synchronous functions in the same module are followed
for one hop. Propagated facts show the call site, callee, argument bindings, and
callee evidence. Recursion, a second hop, ambiguous names, and unsupported
callees remain visible boundaries.

Saga reports explicit exceptions that may leave the selected function. It
removes exceptions caught by a statically clear local handler and carries
exceptions through the supported one-hop call. Bare re-raises keep their type
uncertainty. Unknown exception classes, handler matching, and possible
context-manager suppression remain visible limits. Saga does not infer implicit
Python exceptions or exception behavior from unresolved calls.

The JSON card keeps one record per boundary occurrence. Human views group
occurrences with the same target and stop reason. Important external, dynamic,
mutation-related, and exception boundaries stay visible; routine unresolved
calls are collapsed. In terminal output, use `--show-routine-boundaries` to
expand routine groups and `--show-boundary-sites` to list every location in a
repeated group.

To run its focused tests:

```sh
npm test
uv run python -m unittest discover -s tests
```

To inspect the fixture target from the CLI:

```sh
uv run saga inspect fixture/process_order.py::process_order --format terminal
```

Add `--view return`, `--view mutation`, `--view failure`, or
`--view boundary` when one question matters more than the whole card. These are
fixed filters over the same evidence: claim IDs, source spans, details, and
limiting boundaries do not change. Omit `--view` to return to the full card.
Focused views keep evidence from module-local callees collapsed beneath each
call site. Use `--show-local-call-evidence` to expand it in the terminal; the
editor exposes the same detail through expandable local-call groups.

Focused cards put a short answer before evidence detail. Repeated write, effect,
and failure records are grouped without removing their source spans or boundary
links. Use `--show-claim-evidence` to expand claim provenance and every grouped
record in terminal output. Limits linked to the displayed answer appear before
other direct limits, one-hop limits, and routine unresolved calls.

Return paths with more than eight dependency sites are grouped by the kind of
evidence already recorded on the claim. Use `--show-return-sites` to list every
site in terminal output; the editor expands the same entries under the return
path. Smaller paths keep their source links expanded.

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

To measure the static refresh path after a warm-up:

```sh
uv run python scripts/benchmark_inspect.py \
  fixture/process_order.py::process_order
```

The integration and developer-evaluation protocol is in
`docs/poc-evaluation.md`.

To try the editor flow, open this repository in VS Code, install the repository
as an extension through the Extension Development Host (`Run Extension`), and
open `fixture/process_order.py`. Place the cursor on `process_order`, then use
`Saga: Open Evidence Card`. In the card, choose `Run tests for this function`
to see derived and observed claims in separate sections. Source links navigate
back to the supporting code. Use the tabs to narrow the card to returns, writes
and effects, escaping failures, or analysis limits. Focused views count only the
evidence currently shown; evidence propagated from a local call stays collapsed
under that call until you inspect it. The hover and command palette open the
same focused views directly.
