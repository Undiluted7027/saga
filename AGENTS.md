# Saga: evidence-backed Python code intelligence

Saga analyzes Python code and optional test runs. Its main artifact is a function evidence card shown beside the code.

A useful card answers questions that normally require tracing code:

- Which inputs are rejected, and which exceptions can escape?
- What may affect the return value?
- Which state does the function attempt to write, and which known external effects can it cause?
- Which properties were observed only in tests?
- Where did analysis stop, and which source lines support each result?

Saga is useful only when those answers save work or prevent a wrong conclusion. Restating the function body does not count.

## Sources of truth

This file defines Saga's product boundaries and how code in this repository should be written. [`docs/poc.md`](docs/poc.md) defines the current build: supported behavior, exclusions, slices, build order, acceptance criteria, and completion.

If the two conflict, preserve the product boundaries in this file and use `docs/poc.md` for implementation scope. Ask Sanchit only when the conflict cannot be resolved safely.

## Important

Code must be readable, reviewable, and maintainable. If its assumptions, failure modes, or soundness boundary cannot be explained, the implementation is not acceptable.

Prefer a few tests that attack difficult behavior over a large collection of happy-path tests written for coverage.

Build in vertical slices. Every slice must end in something a developer can see in the evidence card. Keep infrastructure inside the first slice that uses it.

## Product rules

- Saga starts with code and executions. It does not accept natural-language claims and translate them into predicates for verification.
- Every claim records its evidence, assumptions, source spans, and any boundary that limits it.
- Keep derived facts and test observations separate. Never call observed behavior proven.
- Keep claims, boundaries, and diagnostics separate. An opaque boundary is not a low-confidence claim, and unsupported syntax is not evidence.
- Show unsupported behavior and unresolved calls. Never imply purity, completeness, or absence of effects across them.
- Do not infer developer intent as fact.
- The structured result is the source of truth. Editor and CLI renderers may shorten it but must not strengthen or reinterpret it.
- The editor is the primary human interface. The CLI provides focused inspection of a code location.
- Markdown and YAML may be exports. Developers should not have to read a second generated file, and Saga should not inject generated docstrings into their source.
- Do not use an LLM to assign or upgrade evidence classes. An LLM may consume Saga evidence later, but its prose cannot change what Saga established.
- Do not mutate analyzed source files by default.

The same evidence may later feed pull-request findings and coding agents. Do not build either before the POC earns a `go`. PR integration should report behavioral changes that matter to a review instead of dumping complete cards.

## Engineering preferences

- Keep the system small. Apply YAGNI aggressively.
- Prefer established algorithms and libraries when they meet the current slice's needs.
- Use precise types for claims, evidence, assumptions, source spans, boundaries, and diagnostics.
- Keep static analysis deterministic. Dynamic results must record their environment, inputs, and supporting tests.
- Keep derivation logic separate from rendering.
- Comments should explain soundness assumptions and non-obvious analysis choices. Keep them synchronized with the code.
- Propose broad configuration or tooling changes before applying them.
- Be careful with destructive actions and generated artifacts.

## Python preferences

- Target the Python version and syntax listed in `docs/poc.md`. Do not imply support beyond it.
- Use the standard-library `ast` module unless a current slice demonstrates the need for another parser.
- Do not build type inference from scratch. Use explicit annotations or an established type checker when a feature needs type information.
- Treat overloaded operators, descriptors, dynamic attribute access, monkey-patching, runtime imports, native extensions, and unresolved dispatch conservatively.
- Separate syntactic facts, modeled semantic facts, and runtime observations in the schema and implementation.
- Unknown or unsupported behavior must produce a specific boundary or diagnostic instead of silently disappearing.

## POC implementation

Follow the slices and dependency order in [`docs/poc.md`](docs/poc.md). The POC begins with a mocked editor experience, then replaces fixture evidence with real analysis one visible capability at a time.

Use one issue or epic per slice. Put its technical work in an implementation checklist. Split work into another issue only when the slice becomes too large to review or assign as one unit.

Do not add package-wide analysis, interprocedural summaries, concept assignment, rationale inference, CI policy, pull-request automation, source injection, or LLM integration before the POC decision is `go`.

## Testing

- Test correct derivation, conservative refusal, and precise source attribution for every claim producer.
- Test degradation behavior with overloaded operators, descriptors, dynamic dispatch, aliased imports, nested control flow, mutation through calls, and unresolved callees when relevant to the slice.
- Snapshot the structured result and renderers where useful, but assert important semantics directly.
- Dynamic tests must count distinct inputs and record their observed domain. Repeated identical executions must not inflate support.

## Product validation

Test the evidence card with developers before building a broad analyzer. Stop or rethink Saga if most claims restate the source, useful questions end at opaque boundaries, developers ignore the card, reading it takes longer than tracing the code, or nobody notices when Saga is removed.
