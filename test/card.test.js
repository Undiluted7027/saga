const test = require('node:test');
const assert = require('node:assert/strict');
const { loadCard, targetNameFromLine, validateCard, claimPresentation, boundaryGroups, hoverLines } = require('../card');

test('fixture validates against the evidence-card contract', () => {
  const card = loadCard();
  assert.deepEqual(validateCard(card), []);
  assert.equal(card.schema_version, '0.1');
  for (const claim of card.claims) assert.ok(claim.source_spans.length, claim.id);
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
  assert.match(lines.at(-1), /Open detailed evidence card/);
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

test('editor claim presentation preserves a local call chain', () => {
  const claim = structuredClone(loadCard().claims.find((item) => item.kind === 'return_dependency'));
  claim.call_chain = [{ caller: 'process_order', callee: 'lookup_tax', invoked_as: 'lookup_tax', via_alias: false, call_site: claim.source_spans[0], callee_span: claim.source_spans[1], argument_bindings: [{ parameter: 'tax_rate', argument: 'tax_rate', source_span: claim.source_spans[0] }] }];
  const view = claimPresentation(claim);
  assert.equal(view.callChain[0].callee, 'lookup_tax');
  assert.equal(view.callChain[0].argument_bindings[0].parameter, 'tax_rate');
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

test('invalid cards produce actionable validation errors', () => {
  assert.deepEqual(validateCard({}), ['missing schema_version', 'missing target', 'missing claims', 'missing boundaries', 'missing diagnostics', 'schema_version must be 0.1', 'target.status must be supported or unsupported', 'claims must be an array', 'boundaries must be an array']);
});
