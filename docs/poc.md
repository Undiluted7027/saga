# Saga proof of concept

## What we are building

The Saga POC puts evidence about Python behavior inside the editor. Given a function and optional test runs, Saga shows a short hover summary and a detailed evidence panel. Claims link to their source and say whether they were derived or observed. Separate boundary records show where analysis stopped.

The POC succeeds if this helps a developer understand unfamiliar code without tracing every relevant statement or leaving the editor.

## The demo

The demo opens a small unfamiliar Python project in VS Code and inspects one function that validates input, calculates a return value, mutates state, calls an external dependency, and is exercised by tests.

1. The developer hovers over the function.
2. Saga shows rejected inputs and explicit exceptions derived from guards.
3. Saga shows the statements and values that may affect the return value.
4. Saga shows attempted writes and known effects.
5. Saga identifies an unresolved external call as an opaque boundary.
6. Saga shows one property observed across distinct test executions and the observed input domain.
7. The developer expands a claim and jumps to the source that supports it.
8. The developer changes the function and reruns analysis. The card updates without editing or opening a generated documentation file.

The integrated flow works on the fixture and falls apart on more demanding functions. We are fixing those gaps before putting Saga in front of outside developers. Package documentation, pull-request automation, agent integrations, concept assignment, and rationale inference remain out of scope.

## POC question

Can a small set of source-linked behavioral claims help a developer understand an unfamiliar Python function faster or more accurately than reading the function and following its calls unaided?

This POC tests whether the evidence card is useful and whether its claims stay honest within a deliberately small Python subset.

## Current decision

The integrated POC earned a `revise`. Saga produces source-linked derived and observed claims quickly. On non-curated code, the return explanations get unwieldy, unresolved boundaries crowd out useful facts, and analysis often stops at a local helper.

Developer sessions can wait. Each new slice must answer a concrete question on non-curated code and leave the card visibly more useful.

## Product boundary

The POC and capability revision cover:

- Python 3.12 source files.
- Module-level synchronous functions.
- One function selected by `path::qualified_name`.
- A versioned structured evidence schema.
- Entry-guard and explicit-exception analysis.
- Intraprocedural return dependencies.
- Bounded one-hop summaries for module-local calls during the capability revision.
- Explicit escaping exceptions found in the selected function or a supported local callee.
- Direct write and known-effect detection.
- Explicit opaque-boundary reporting.
- A small set of test-observed properties.
- A compact CLI rendering.
- A VS Code hover summary and detailed evidence panel backed by the same structured result.
- Navigation from claims to source locations.
- A fixture project that demonstrates supported and unsupported behavior.

They do not include:

- Free-form documentation generation.
- Natural-language claim verification.
- LLM calls inside Saga.
- Source-file mutation or generated docstrings.
- General symbolic execution or whole-program verification.
- Arbitrary Python versions.
- Async functions, generators, nested functions, or runtime-generated code.
- Recursive, whole-program, or package-wide interprocedural analysis.
- Complete type inference.
- Concept assignment or rationale inference.
- Pull-request comments, CI policy, or coding-agent integrations.
- A hosted service, accounts, telemetry, or billing.
- Performance work beyond keeping the demo responsive.

Unsupported constructs produce a specific boundary or diagnostic. They never disappear silently.

### Supported Python subset

The POC analyzes Python 3.12 module-level synchronous functions. The target function may have typed or untyped parameters and may contain:

- Local, attribute, subscript, and global assignments, including annotated and augmented assignments, plus `global` declarations.
- Expression statements containing calls.
- `if`/`else`, `assert`, `raise`, `return`, `for`, and `with` statements.
- Names, constants, attribute and subscript access, calls, boolean operations, comparisons, and unary and binary operations within those statements.
- A function docstring and `pass`, which produce no behavioral claims.

Syntax support is wider than semantic support. The POC follows these rules:

- Local-name reads and writes use lexical scope within the target function.
- `if` branches and `for` loops receive conservative control-flow edges. Loop analysis may over-approximate dependencies.
- Guard conditions are preserved structurally. Operators are not assumed to have builtin behavior unless the claim records that assumption.
- Calls are opaque unless a small, explicit registry or a bounded module-local summary provides relevant behavior. Construction of a modeled builtin exception in a `raise` statement is handled by the guard analysis.
- Attribute and subscript assignments are reported as attempted write operations. They also produce a boundary for assignment-hook behavior unless Saga can establish modeled builtin semantics. They do not prove that ordinary mutation occurred because descriptors, `__setattr__`, and `__setitem__` may run arbitrary code.
- An `assert`-based claim records the assumption that `__debug__` is true. Python may remove assertions when run with optimization.
- Typing overload declarations are skipped when Saga can identify one concrete implementation. A decorator on that implementation limits every body-derived claim because the runtime wrapper remains unknown.
- Saga traverses a `with` body, but marks its effects and returns with a context-manager boundary. It does not model entry, exit, or exception suppression.

Any other statement or expression produces a diagnostic and suppresses claims that would depend on semantics Saga does not model. An unresolved call or dynamic operation produces a boundary attached to the affected analysis.

## Evidence model

All surfaces consume the same structured result. Renderers may shorten a claim but cannot strengthen or reinterpret it.

The schema has these parts:

```python
class EvidenceCard:
    schema_version: str
    target: FunctionTarget
    claims: list[Claim]
    boundaries: list[Boundary]
    diagnostics: list[Diagnostic]

class Claim:
    id: str
    kind: ClaimKind
    statement: StructuredStatement
    evidence: Evidence
    source_spans: list[SourceSpan]
    assumptions: list[Assumption]
    boundary_ids: list[str]

class Evidence:
    method: EvidenceMethod
    evidence_class: EvidenceClass
    detail: dict[str, object]

class Boundary:
    id: str
    kind: BoundaryKind
    target: StructuredTarget
    reason: str
    source_span: SourceSpan
```

The Python representation may change. The serialized form must remain versioned, and unstructured prose cannot become its source of truth.

The POC has two evidence classes:

- `derived`: computed within a documented static model.
- `observed`: held across recorded executions and includes support and domain details.

Opaque behavior is a `Boundary`, not an evidence status. Unsupported syntax or semantics produces a `Diagnostic`. A claim limited by a boundary refers to its ID and cannot imply completeness. Evidence classes describe provenance; they are not scores or ranks.

The POC claim kinds are rejected input, explicit exception, attempted write, known effect, return dependency, and test observation. Boundary kinds cover unresolved calls, dynamic dispatch, assignment hooks, and unsupported semantics. Diagnostics cover failures that prevent or limit analysis, including parsing, target selection, unsupported syntax, configuration, and instrumentation errors.

## Vertical slices

Slices 0 through 6 record the first build. Each slice ends in behavior a developer can see. Infrastructure belongs inside the first slice that uses it.

### Slice 0: validate the evidence-card interaction

**Outcome:** A developer can view and navigate a realistic evidence card inside VS Code, backed by fixture data rather than a real analyzer.

**Why first:** Test the product idea before spending time on the analyzer.

**Demo:** Open the fixture `process_order` function, hover it, expand the card, and jump from the return-dependency claim to three source lines.

**Acceptance criteria:**

- The fixture card contains a guard, exception, return dependency, effect, observation, and opaque boundary.
- The hover summary uses no more than six short lines; claim details remain in the panel.
- A developer can open the detailed card on demand.
- Every displayed claim links to one or more source spans.
- The editor consumes the same serialized schema intended for real analysis.
- At least three developers attempt a short code-understanding task with the prototype, and their confusion and unused information are recorded.

**Not included:** Real analysis, caching, background indexing, or visual polish beyond a usable prototype.

**Implementation checklist:**

1. Define the versioned evidence-card schema and fixture payload.
2. Build a minimal VS Code hover and detail view for a fixture function.
3. Add source navigation for evidence spans.
4. Run and record the first evidence-card usability sessions.

### Slice 1: inspect a real function

**Outcome:** `saga inspect path.py::function` locates a real Python function and returns a structured card with target metadata and diagnostics.

**Demo:** Run the command against a module-level function and show its signature, file span, supported status, and an empty claim collection in both JSON and terminal output.

**Acceptance criteria:**

- Saga parses a Python 3.12 file with the standard-library `ast` module.
- The selector resolves exactly one module-level synchronous function.
- Missing, ambiguous, malformed, and unsupported targets produce actionable diagnostics.
- JSON output validates against the schema from Slice 0.
- Terminal output is a renderer over the structured result.
- The VS Code prototype can request and display the real result.

**Not included:** Behavioral claims, package indexing, methods, async functions, or import resolution.

**Implementation checklist:**

1. Scaffold the Python package and `saga inspect` command.
2. Implement file parsing and function selection.
3. Implement schema validation and JSON serialization.
4. Connect the VS Code prototype to real `saga inspect` output.

### Slice 2: show rejected inputs and explicit failures

**Outcome:** The card shows entry guards and explicit exceptions without claiming more than the syntax supports.

**Demo:** Inspect a function with `if amount <= 0: raise ValueError` and an `assert account.active`. The card shows both conditions and source spans. It records that the assertion depends on `__debug__` and can disappear under Python optimization.

**Acceptance criteria:**

- Saga recognizes a contiguous entry prefix of parameter-only `if`/`raise` guards and assertions.
- A guard claim records the condition as written, the exit behavior, and both source spans.
- An assertion claim records the `__debug__` assumption and is not presented as an unconditional runtime check.
- Boolean negation is represented structurally rather than generated as unchecked prose.
- Conditions involving calls, attribute hooks, overloaded operators, or unsupported expressions include explicit assumptions or are reported as unsupported.
- Nested branches and guards after state-changing statements are not mislabeled as unconditional entry requirements.
- The CLI and editor render the new claims from the shared schema.

**Not included:** Weakest-precondition calculation, inferred exceptions from callees, Z3, or general postcondition inference.

**Implementation checklist:**

1. Define structured guard and explicit-exception statements.
2. Extract entry guards and assertions conservatively.
3. Render input and failure claims in CLI and editor surfaces.
4. Add adversarial guard fixtures and degradation tests.

### Slice 3: show direct effects and analysis boundaries

**Outcome:** The card shows attempted writes, known effectful calls, and unresolved behavior.

**Demo:** Inspect a function that assigns `order.status`, writes through `pathlib.Path.write_text`, and calls `payment_gateway.charge`. Saga reports an attempted attribute write and a registry-backed filesystem effect. It reports the gateway call as an opaque boundary.

**Acceptance criteria:**

- Saga detects attempted writes to attributes, subscripts, and globals within the target function.
- Attribute and subscript write results attach a boundary for descriptors or assignment hooks unless Saga knows the receiver has modeled builtin semantics.
- A small, reviewable registry classifies selected standard-library calls by may-effect.
- Import aliases used by supported registry entries resolve correctly within the module.
- Unknown calls produce boundaries that name the callee, source span, and resolution failure.
- Saga does not claim purity when an unknown call or unsupported operation exists.
- Claims distinguish syntactic write operations from registry-backed effects. Boundaries represent unresolved behavior.
- The CLI and editor render effects and boundaries without hiding duplicates that have different sources.

**Not included:** User-defined callee summaries, framework plugins, runtime monkey-patching, or proof of effect absence.

**Implementation checklist:**

1. Define write, effect, and boundary statements.
2. Detect direct writes in the function body.
3. Add import-aware lookup for a small standard-library effect registry.
4. Emit opaque boundaries for unresolved calls.
5. Render and test effect evidence in the function card.

### Slice 4: explain the return value

**Outcome:** A developer can see which parameters and statements may affect a function's return value and navigate to them.

**Demo:** Inspect `process_order` and expand "Return may depend on." Saga links the result to validation branches, subtotal accumulation, tax lookup, and discount selection while excluding an unrelated constant assignment.

**Acceptance criteria:**

- Saga builds an intraprocedural control-flow representation for supported statements.
- Reaching definitions connect reads to possible writes.
- Control dependencies retain predicates that determine whether a reaching definition or return executes.
- A backward traversal from each return produces a conservative may-affect slice.
- Multiple returns remain distinguishable in the structured result.
- Unknown calls in the slice attach an opaque boundary rather than disappearing.
- The editor can navigate from the return claim to every included source span.
- Tests cover branches, reassignment, loops treated conservatively, and unrelated statements.

**Not included:** Minimal semantic slicing, interprocedural slicing, alias analysis beyond explicitly documented rules, or natural-language summaries of the algorithm.

**Implementation checklist:**

1. Define return-output and dependency statements.
2. Build the supported control-flow representation.
3. Implement reaching definitions and control dependencies.
4. Compute backward may-affect slices from returns.
5. Add return-slice rendering and multi-span navigation.

### Slice 5: add test-observed evidence

**Outcome:** The card shows a small number of useful properties observed during real tests and labels them as observations.

**Demo:** Run the fixture tests through Saga. The card reports that the numeric return value was non-negative for a stated number of distinct inputs, names the supporting tests, and shows the observed ranges. A repeated identical input does not increase distinct support.

**Acceptance criteria:**

- An explicit Saga test command runs pytest with opt-in instrumentation for selected targets.
- Instrumentation records arguments, returns, and raised exceptions at function boundaries.
- Trace storage has a documented, versioned format and does not serialize unsupported or sensitive values indiscriminately.
- A small fixed template set evaluates candidate observations.
- Constant-only and duplicate-input observations are suppressed or clearly identified.
- Every observed claim records distinct support, supporting test IDs, and a compact input-domain summary.
- A violated candidate is omitted rather than weakened into suggestive prose.
- Static and observed claims remain separate in the schema and UI.

**Not included:** Arbitrary invariant synthesis, `sys.settrace` coverage of every function, statistical claims of proof, or model-generated candidates.

**Implementation checklist:**

1. Define the trace and observed-evidence schemas.
2. Add opt-in pytest boundary instrumentation.
3. Implement distinct-input accounting and domain summaries.
4. Implement the initial observation templates and suppression rules.
5. Render observed evidence in the CLI and editor.

### Slice 6: integrate and dogfood the first POC

**Outcome:** The real analyzer drives the complete editor demo and exposes the gaps that block a useful developer evaluation.

**Demo:** Run the full flow described in "The demo" from a clean checkout, including a source edit and refreshed card.

**Acceptance criteria:**

- The fixture contains supported examples and deliberate opaque or unsupported cases.
- After a warm-up run, the complete static card refreshes within one second on the recorded evaluation machine for the fixture target. Record median and p95 latency across at least 20 runs.
- Analysis failures appear as diagnostics without breaking unrelated claims.
- The demo runs from documented setup commands on a clean machine or reproducible environment.
- Saga is run against non-curated functions as well as the demo fixture.
- The findings record correctness failures, noisy output, unclear claims, and rough editor interactions.
- The POC findings produce a written `go`, `revise`, or `stop` decision.

**Not included:** Production hardening or features added solely to make the demo look broader.

**Implementation checklist:**

1. Build the final fixture and scripted demo path.
2. Add refresh behavior and basic analysis caching.
3. Harden partial-failure diagnostics.
4. Dogfood the card on non-curated functions.
5. Write the POC decision memo.

### Capability revision

The next work stays on the function card and stops well short of a general Python analyzer.

1. Make claims answer the question directly. Render guards, return paths, mutations, and failures in terms a developer can scan without decoding AST-shaped data.
2. Reduce boundary noise without hiding uncertainty. Group repeated low-signal calls and keep external, dynamic, or behavior-changing boundaries prominent.
3. Follow one module-local call when it materially improves a card. Reuse the same evidence rules, stop at recursion or unsupported dispatch, and show the call chain behind propagated facts.
4. Report explicit exceptions that can escape the selected function, including exceptions propagated through a supported local summary.
5. Add focused views for return dependencies, mutations, failures, and analysis boundaries. These views operate on Saga's structured evidence and do not accept natural-language claims.

Dogfood each slice on real functions that were not written to flatter the analyzer. Keep examples that expose failure modes in the test suite.

## Slice dependency order

```text
Slice 0: evidence-card UX
    |
    v
Slice 1: real function inspection
    |
    +--> Slice 2: guards and failures ----+
    |                                     |
    +--> Slice 3: effects and boundaries -+--> Slice 6: integration and dogfooding
    |                                     |
    +--> Slice 4: return dependencies ----+
    |                                     |
    +--> Slice 5: observed evidence ------+
                                          |
                                          v
                              capability revision slices
                                          |
                                          v
                                developer evaluation
```

After Slice 1, Slices 2 through 5 can proceed independently against the shared schema. Finish and evaluate the smallest useful combination before starting more work.

## Issue extraction rules

When moving this plan to GitHub:

- Create one milestone for the POC and one issue or epic for each vertical slice.
- Use each slice's implementation checklist inside that slice issue. Split a checklist item into a separate issue only when the slice becomes too large to review or assign as one unit.
- Every split issue must name the user-visible behavior it enables.
- Keep infrastructure tasks inside the feature that consumes them. Do not create open-ended "build the analyzer" or "design the architecture" tickets.
- Copy acceptance criteria into issues as checkboxes and preserve exclusions.
- Link each ticket to its slice and to the POC demo.
- Close or rewrite downstream tickets when an earlier slice invalidates their assumptions.
- During the `revise` phase, create tickets only for capability gaps observed while dogfooding the function card.

## POC completion

The POC is done when:

- A developer can inspect a real supported Python function from VS Code without opening a generated documentation file.
- The card includes source-linked derived and observed claims plus separate boundary records.
- Saga updates the card after the source changes.
- Unsupported behavior is visible and specific.
- The CLI and editor agree because they render the same structured result.
- The complete demo works from a clean setup.
- Dogfooding shows that the card can answer concrete questions on non-curated functions without obvious correctness failures or boundary overload.
- Developer sessions then provide enough evidence for an explicit `go`, `revise`, or `stop` decision.

Completion does not require package-wide analysis, CI, pull-request integration, coding-agent integration, concept assignment, rationale inference, or publication-quality documentation.

## After the POC

Create a later-work document only after a `go` decision. Base it on what developers did with the card, not the old speculative roadmap. Package-scale analysis, pull-request findings, and coding-agent context can wait until the function card saves real tracing work.
