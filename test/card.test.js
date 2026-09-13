const test = require('node:test');
const assert = require('node:assert/strict');
const { loadCard, targetNameFromLine, validateCard, claimPresentation, claimGroups, focusedAnswer, prioritizeBoundaryGroups, returnPathPresentation, focusCard, localCallEvidence, viewPresentation, observationPresentation, boundaryGroups, diagnosticGroups, hoverLines } = require('../card');

test('fixture validates against the evidence-card contract', () => {
  const card = loadCard();
  assert.deepEqual(validateCard(card), []);
  assert.equal(card.schema_version, '0.1');
  for (const claim of card.claims) assert.ok(claim.source_spans.length, claim.id);
});

test('the evidence-card JSON schema is parseable', () => {
  const schemaPath = require('node:path').join(__dirname, '..', 'schema', 'evidence-card.schema.json');
  const schema = JSON.parse(require('node:fs').readFileSync(schemaPath, 'utf8'));
  assert.equal(schema.$defs.claim.type, 'object');
});

test('target selection uses the function name rather than the word under the cursor', () => {
  assert.equal(targetNameFromLine('def process_order(order):'), 'process_order');
  assert.equal(targetNameFromLine('async def process_order(order):'), 'process_order');
  assert.equal(targetNameFromLine('    def nested(order):'), undefined);
});

test('fixture exercises every Slice 0 result kind', () => {
  const card = loadCard();
  assert.deepEqual(new Set(card.claims.map((claim) => claim.kind)), new Set(['rejected_input', 'explicit_exception', 'return_dependency', 'attempted_write', 'known_effect', 'test_observation']));
  assert.ok(card.boundaries.some((boundary) => boundary.kind === 'unresolved_call'));
});

test('hover stays compact and leaves details for the panel', () => {
  const lines = hoverLines(loadCard());
  assert.ok(lines.length <= 6, `hover has ${lines.length} lines`);
  assert.match(lines.at(-1), /Full.*Return.*Mutation.*Failure.*Boundaries/);
  assert.ok(lines.some((line) => line.includes('Observed')));
  assert.ok(lines.some((line) => line.includes('Boundary')));
  assert.ok(lines.some((line) => line.includes('order.items is falsy')));
});

test('editor claim presentation keeps wording and supporting evidence together', () => {
  const claim = loadCard().claims.find((item) => item.kind === 'rejected_input');
  const view = claimPresentation(claim);
  assert.equal(view.summary, 'Rejects input when order.items is falsy.');
  assert.equal(view.sourceText, 'not order.items');
  assert.equal(view.evidenceClass, 'derived');
  assert.equal(view.method, 'entry_guard');
  assert.deepEqual(view.sourceSpans, claim.source_spans);
});

test('editor groups repeated write records without losing evidence', () => {
  const card = loadCard();
  const write = card.claims.find((item) => item.kind === 'attempted_write');
  const repeated = structuredClone(write);
  repeated.id = 'second-write';
  repeated.source_spans[0].start_line += 1;
  const claims = [write, repeated];
  const before = structuredClone(claims);
  const groups = claimGroups(claims);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].count, 2);
  assert.deepEqual(groups[0].claimIds, [write.id, 'second-write']);
  assert.deepEqual(groups[0].claims, claims);
  assert.deepEqual(claims, before);
});

test('editor gives the focused answer before presentation detail', () => {
  const card = focusCard(loadCard(), 'mutation');
  const answer = focusedAnswer(card, card.claims);
  assert.match(answer.headline, /recorded write site/);
  assert.match(answer.detail, /Unresolved calls remain separate/);
});

test('editor mutation answer counts evidence inside local calls', () => {
  const card = loadCard();
  const write = structuredClone(card.claims.find((item) => item.kind === 'attempted_write'));
  write.call_chain = [{ callee: 'helper' }];
  const answer = focusedAnswer({ view: 'mutation', boundaries: [] }, [write]);
  assert.equal(answer.headline, '1 write site inside local calls.');
});

test('editor ranks linked caller boundaries without dropping any group', () => {
  const card = focusCard(loadCard(), 'mutation');
  const groups = boundaryGroups(card);
  const before = structuredClone(groups);
  const tiers = prioritizeBoundaryGroups(groups, card.claims, 'mutation');
  const linked = new Set(card.claims.flatMap((claim) => claim.boundary_ids));
  for (const group of tiers.primary) {
    assert.ok(group.boundaryIds.some((id) => linked.has(id)));
    assert.match(group.relevance, /Directly limits/);
  }
  assert.equal(Object.values(tiers).flat().length, groups.length);
  assert.deepEqual(groups, before);
});

test('editor claim presentation preserves a local call chain', () => {
  const claim = structuredClone(loadCard().claims.find((item) => item.kind === 'return_dependency'));
  claim.call_chain = [{ caller: 'process_order', callee: 'lookup_tax', invoked_as: 'lookup_tax', via_alias: false, call_site: claim.source_spans[0], callee_span: claim.source_spans[1], argument_bindings: [{ parameter: 'tax_rate', argument: 'tax_rate', source_span: claim.source_spans[0] }] }];
  const view = claimPresentation(claim);
  assert.equal(view.callChain[0].callee, 'lookup_tax');
  assert.equal(view.callChain[0].argument_bindings[0].parameter, 'tax_rate');
});

test('editor claim presentation preserves composed local return dependencies', () => {
  const claim = structuredClone(loadCard().claims.find((item) => item.kind === 'return_dependency'));
  claim.statement.local_call_dependencies = [{
    callee_parameter: 'price',
    callee_scope: 'calculate_total',
    caller_argument: 'catalog[sku]',
    binding_origin: 'argument',
    caller_inputs: ['catalog'],
    argument_span: claim.source_spans[0],
    callee_return_spans: [claim.source_spans[1]],
    boundary_ids: [],
    call_chain: []
  }];
  claim.statement.scope = { kind: 'callee', function: 'calculate_total', names: ['price'] };
  const view = claimPresentation(claim);
  assert.deepEqual(view.localCallDependencies, claim.statement.local_call_dependencies);
  assert.deepEqual(view.scope, claim.statement.scope);
});

test('editor groups large return paths from existing dependency records', () => {
  const claim = structuredClone(loadCard().claims.find((item) => item.kind === 'return_dependency'));
  const sourceSpan = (line) => ({ path: 'module.py', start_line: line, start_column: 4, end_line: line, end_column: 12 });
  const entry = (kind, line, names = [], reads = [], calls = []) => ({
    kind, names, reads,
    calls: calls.map((text) => ({ text, source_span: sourceSpan(line) })),
    source_span: sourceSpan(line)
  });
  claim.statement.return_expression = 'result';
  claim.statement.path_conditions = [{ text: 'enabled is truthy', source_text: 'enabled', source_span: sourceSpan(2) }];
  claim.evidence.detail.return_source_span = sourceSpan(20);
  claim.statement.dependencies = [
    entry('definition', 3, ['result'], ['seed']),
    entry('definition', 4, ['result'], ['item']),
    entry('weak_definition', 5, ['result'], ['factor']),
    entry('control_predicate', 6, [], ['enabled']),
    entry('statement', 7, [], ['result'], ['normalize(...)']),
    entry('statement', 8, [], [], ['audit(...)']),
    entry('statement', 9, [], ['result']),
    entry('statement', 10, [], ['fallback']),
    entry('return', 20, [], ['result'])
  ];
  claim.source_spans = claim.statement.dependencies.map((item) => item.source_span);
  const before = structuredClone(claim);

  const view = returnPathPresentation(claim);

  assert.equal(view.compact, true);
  assert.equal(view.siteCount, 9);
  assert.deepEqual(view.groups.map((group) => group.kind), ['return', 'definition', 'weak_definition', 'control_predicate', 'statement']);
  assert.deepEqual(view.groups.find((group) => group.kind === 'definition').reads, ['item', 'seed']);
  assert.deepEqual(view.groups.find((group) => group.kind === 'statement').calls, ['audit(...)', 'normalize(...)']);
  assert.deepEqual(claim, before);
});

test('editor exception presentation preserves handler evidence', () => {
  const claim = structuredClone(loadCard().claims.find((item) => item.kind === 'explicit_exception'));
  claim.statement.handler_spans = [claim.source_spans[0]];
  const view = claimPresentation(claim);
  assert.equal(view.summary, claim.statement.text);
  assert.deepEqual(view.handlerSpans, claim.statement.handler_spans);
});

test('editor groups repeated boundaries without dropping occurrences', () => {
  const card = loadCard();
  const first = structuredClone(card.boundaries[1]);
  first.id = 'repeat-one';
  const second = structuredClone(first);
  second.id = 'repeat-two';
  second.source_span = { ...second.source_span, start_line: 15, end_line: 15 };
  card.boundaries = [first, second];
  const before = structuredClone(card.boundaries);
  const groups = boundaryGroups(card);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].count, 2);
  assert.deepEqual(groups[0].boundaryIds, ['repeat-one', 'repeat-two']);
  assert.deepEqual(groups[0].occurrences.map((item) => item.sourceSpan.start_line), [6, 15]);
  assert.deepEqual(card.boundaries, before);
});

test('editor boundary groups preserve limits on other boundaries', () => {
  const card = loadCard();
  const call = structuredClone(card.boundaries.find((item) => item.kind === 'unresolved_call'));
  call.id = 'conditional-call';
  call.boundary_ids = ['try-limit'];
  const groups = boundaryGroups({ ...card, boundaries: [call] });
  assert.deepEqual(groups[0].limitingBoundaryIds, ['try-limit']);
  assert.deepEqual(groups[0].occurrences[0].boundaryIds, ['try-limit']);
});

test('editor groups repeated diagnostics without changing the raw reports', () => {
  const card = loadCard();
  card.diagnostics = [
    { kind: 'unsupported_semantics', message: 'Try is unsupported.', source_span: card.target.source_span },
    { kind: 'unsupported_semantics', message: 'Try is unsupported.', source_span: card.target.source_span },
    { kind: 'unsupported_semantics', message: 'Continue is unsupported.', source_span: card.target.source_span }
  ];
  const before = structuredClone(card.diagnostics);
  const groups = diagnosticGroups(card);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].reportCount, 2);
  assert.equal(groups[0].siteCount, 1);
  assert.deepEqual(card.diagnostics, before);
});

test('editor keeps different stop reasons and boundary classes separate', () => {
  const card = loadCard();
  const external = structuredClone(card.boundaries[1]);
  const local = { ...structuredClone(external), id: 'local', kind: 'local_call_limit', reason: 'One-hop limit.' };
  const routine = { ...structuredClone(external), id: 'routine', target: { text: 'len(...)' }, category: 'routine' };
  card.boundaries = [external, local, routine];
  const groups = boundaryGroups(card);
  assert.deepEqual(groups.map((item) => item.boundaryClass), [
    'external_or_unresolved_call',
    'module_local',
    'routine_call'
  ]);
});

test('editor focused views retain claim identity and related boundaries', () => {
  const card = loadCard();
  for (const [view, kinds] of [
    ['return', ['return_dependency']],
    ['mutation', ['attempted_write', 'known_effect']],
    ['failure', ['rejected_input', 'explicit_exception']]
  ]) {
    const focused = focusCard(card, view);
    assert.deepEqual([...new Set(focused.claims.map((claim) => claim.kind))].sort(), kinds.sort());
    for (const claim of focused.claims) {
      assert.equal(card.claims.find((item) => item.id === claim.id), claim);
      assert.deepEqual(card.claims.find((item) => item.id === claim.id).source_spans, claim.source_spans);
    }
    const related = new Set(focused.claims.flatMap((claim) => claim.boundary_ids));
    assert.deepEqual(new Set(focused.boundaries.map((item) => item.id)), related);
  }
});

test('editor mutation view retains effect-relevant boundaries without inventing claims', () => {
  const card = loadCard();
  card.claims = [];
  card.boundaries = [
    { ...structuredClone(card.boundaries[1]), id: 'notify', target: { text: 'notify(...)' }, concerns: ['effects'] },
    { ...structuredClone(card.boundaries[1]), id: 'calculate', target: { text: 'calculate(...)' } },
    { ...structuredClone(card.boundaries[1]), id: 'len', target: { text: 'len(...)' }, category: 'routine' }
  ];
  const focused = focusCard(card, 'mutation');
  assert.deepEqual(focused.claims, []);
  assert.deepEqual(focused.boundaries.map((item) => item.id), ['notify']);
  assert.equal(viewPresentation(focused).empty, false);
});

test('editor failure view retains unresolved exception sources without inventing claims', () => {
  const card = loadCard();
  card.claims = [];
  card.boundaries = [
    { ...structuredClone(card.boundaries[1]), id: 'callback', target: { text: 'callback(...)' }, concerns: ['exceptions'] },
    { ...structuredClone(card.boundaries[1]), id: 'effect', target: { text: 'notify(...)' }, concerns: ['effects'] },
    { ...structuredClone(card.boundaries[1]), id: 'len', target: { text: 'len(...)' }, category: 'routine' }
  ];

  const focused = focusCard(card, 'failure');

  assert.deepEqual(focused.claims, []);
  assert.deepEqual(focused.boundaries.map((item) => item.id), ['callback']);
  assert.equal(viewPresentation(focused).empty, false);
  assert.deepEqual(focusedAnswer(focused, focused.claims), {
    headline: 'No supported explicit failure claims.',
    detail: 'Unresolved calls may still raise; their exception limits are listed below.'
  });
});

test('editor mutation view puts potentially shared writes before local construction', () => {
  const card = loadCard();
  const shared = structuredClone(card.claims.find((claim) => claim.kind === 'attempted_write'));
  shared.id = 'shared-write';
  shared.statement.write_scope = 'potentially_aliased';
  const local = structuredClone(shared);
  local.id = 'local-write';
  local.statement.write_scope = 'local_container';

  const focused = focusCard({ ...card, claims: [local, shared] }, 'mutation');

  assert.deepEqual(focused.claims.map((claim) => claim.id), ['shared-write', 'local-write']);
  assert.equal(focusedAnswer(focused, focused.claims).headline, '1 recorded write site across 1 target and 1 local-container write site.');
});

test('focused editor views preserve test observation status', () => {
  const card = loadCard();
  card.observation_status = {
    state: 'no_claim',
    template: 'numeric_return_non_negative',
    reason: 'unsupported_return_shape',
    message: 'No template supports this return shape.',
    execution_count: 2,
    returned_executions: 2,
    raised_executions: 0,
    distinct_inputs: 0,
    distinct_outputs: 0,
    excluded_parameters: [{ name: 'fetch_balance', serialization_kinds: ['unsupported'], types: ['builtins.function'] }],
    input_domain: { fetch_balance: { kind: 'excluded', serialization_kinds: ['unsupported'], types: ['builtins.function'] } },
    tests: ['test_report'],
    environment: { python_version: '3.12' }
  };
  assert.equal(focusCard(card, 'return').observation_status, card.observation_status);
  const presentation = observationPresentation(card.observation_status);
  assert.equal(presentation.distinctInputSummary, '0 distinct usable inputs');
  assert.equal(presentation.excludedParameters[0].name, 'fetch_balance');
});

test('editor boundary and empty views do not imply completeness', () => {
  const card = loadCard();
  const boundary = focusCard(card, 'boundary');
  assert.equal(boundary.claims.length, 0);
  assert.deepEqual(boundary.boundaries, card.boundaries);

  const empty = focusCard({ ...card, claims: [], boundaries: [] }, 'failure');
  const presentation = viewPresentation(empty);
  assert.equal(presentation.empty, true);
  assert.match(presentation.emptyMessage, /does not establish/);
  assert.match(presentation.emptyMessage, /cannot fail/);
});

test('full editor view returns the original card', () => {
  const card = loadCard();
  assert.equal(focusCard(card, 'full'), card);
});

test('focused editor view keeps analysis diagnostics', () => {
  const card = loadCard();
  card.target.status = 'unsupported';
  card.diagnostics = [{ kind: 'unsupported_target', message: 'Async target.' }];
  assert.equal(focusCard(card, 'return').diagnostics[0].kind, 'unsupported_target');
  card.target.status = 'supported';
  card.diagnostics = [{ kind: 'unsupported_syntax', message: 'Unsupported statement.' }];
  assert.equal(focusCard(card, 'return').diagnostics[0].kind, 'unsupported_syntax');
});

test('focused editor views filter diagnostics and point back to the full card', () => {
  const card = loadCard();
  card.diagnostics = [
    { kind: 'unsupported_semantics', message: 'Return gap.', analyses: ['returns'], source_span: card.target.source_span },
    { kind: 'unsupported_semantics', message: 'Effect gap.', analyses: ['effects'], source_span: card.claims[0].source_spans[0] }
  ];
  const mutation = focusCard(card, 'mutation');
  assert.deepEqual(mutation.diagnostics.map((item) => item.message), ['Effect gap.']);
  assert.equal(mutation.hidden_diagnostics.group_count, 1);
  assert.equal(viewPresentation(mutation).hiddenDiagnosticMessage, '1 diagnostic group hidden; open the full card to inspect them.');
  assert.equal(focusCard(card, 'full'), card);
});

test('editor partitions propagated evidence by first local call site', () => {
  const sourceSpan = (line) => ({ path: 'module.py', start_line: line, start_column: 4, end_line: line, end_column: 12 });
  const callLink = (callee, line) => ({
    caller: 'target',
    callee,
    invoked_as: callee,
    via_alias: false,
    call_site: sourceSpan(line),
    callee_span: sourceSpan(line + 100),
    argument_bindings: []
  });
  const first = callLink('helper', 5);
  const second = callLink('helper', 9);
  const directClaim = { id: 'direct' };
  const propagatedClaim = { id: 'propagated', call_chain: [first] };
  const secondClaim = { id: 'second-call', call_chain: [second] };
  const propagatedBoundary = { id: 'boundary', call_chain: [first] };
  const propagatedDiagnostic = { message: 'Gap', call_chain: [first] };
  const card = {
    claims: [directClaim, propagatedClaim, secondClaim],
    boundaries: [propagatedBoundary],
    diagnostics: [propagatedDiagnostic]
  };
  const before = structuredClone(card);

  const partition = localCallEvidence(card);

  assert.deepEqual(partition.direct.claims, [directClaim]);
  assert.equal(partition.groups.length, 2);
  assert.equal(partition.groups[0].callSite.start_line, 5);
  assert.deepEqual(partition.groups[0].claims, [propagatedClaim]);
  assert.deepEqual(partition.groups[0].boundaries, [propagatedBoundary]);
  assert.deepEqual(partition.groups[0].diagnostics, [propagatedDiagnostic]);
  assert.equal(partition.groups[1].callSite.start_line, 9);
  assert.deepEqual(card, before);
});

test('extension contributes all four focused view actions', () => {
  const manifest = JSON.parse(require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'package.json'), 'utf8'));
  const commands = new Set(manifest.contributes.commands.map((item) => item.command));
  assert.ok(commands.has('saga.openReturnEvidence'));
  assert.ok(commands.has('saga.openMutationEvidence'));
  assert.ok(commands.has('saga.openFailureEvidence'));
  assert.ok(commands.has('saga.openBoundaryEvidence'));
});

test('invalid cards produce actionable validation errors', () => {
  assert.deepEqual(validateCard({}), ['missing schema_version', 'missing target', 'missing claims', 'missing boundaries', 'missing diagnostics', 'schema_version must be 0.1', 'target.status must be supported or unsupported', 'claims must be an array', 'boundaries must be an array']);
});
