const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');

const originalLoad = Module._load;
Module._load = function loadWithVscodeStub(request, parent, isMain) {
  if (request === 'vscode') return {};
  return originalLoad.call(this, request, parent, isMain);
};
const { panelHtml } = require('../extension');
Module._load = originalLoad;

const { focusCard, loadCard } = require('../card');

test('focused return webview groups a large path behind native expansion controls', () => {
  const card = loadCard();
  const claim = structuredClone(card.claims.find((item) => item.kind === 'return_dependency'));
  const sourceSpan = (line) => ({ path: 'module.py', start_line: line, start_column: 4, end_line: line, end_column: 12 });
  claim.statement.return_expression = 'result';
  claim.statement.path_conditions = [{ text: 'enabled is truthy', source_text: 'enabled', source_span: sourceSpan(2) }];
  claim.evidence.detail.return_source_span = sourceSpan(20);
  claim.statement.dependencies = Array.from({ length: 9 }, (_, index) => ({
    kind: index === 8 ? 'return' : 'definition',
    names: index === 8 ? [] : ['result'],
    reads: [`input_${index}`],
    calls: index === 4 ? [{ text: 'normalize(...)', source_span: sourceSpan(index + 3) }] : [],
    source_span: sourceSpan(index + 3)
  }));
  claim.source_spans = [...claim.statement.dependencies.map((item) => item.source_span), sourceSpan(30)];
  const focused = focusCard({ ...card, claims: [claim], boundaries: [], diagnostics: [] }, 'return');

  const html = panelHtml(focused, { webview: { cspSource: 'vscode-webview://test' } });

  assert.match(html, /Return path 1: <code>result<\/code>/);
  assert.match(html, /When: \(enabled is truthy\)/);
  assert.match(html, /<details class="return-sites"><summary>9 dependency sites in 2 groups<\/summary>/);
  assert.match(html, /definition: 8 sites/);
  assert.match(html, /normalize\(\.\.\.\)/);
  assert.match(html, /module\.py:30/);
  assert.doesNotMatch(html, />source 10<\/a>/);
});

test('focused webview collapses propagated evidence beneath a native details group', () => {
  const card = loadCard();
  const direct = structuredClone(card.claims.find((claim) => claim.kind === 'attempted_write'));
  direct.id = 'direct-write';
  direct.statement.text = 'Attempts to write caller state.';
  direct.boundary_ids = [];

  const link = {
    caller: card.target.name,
    callee: 'helper',
    invoked_as: 'helper',
    via_alias: false,
    call_site: card.target.source_span,
    callee_span: direct.source_spans[0],
    argument_bindings: []
  };
  const propagated = structuredClone(direct);
  propagated.id = 'propagated-write';
  propagated.statement.text = 'helper(...) may attempt to write callee state.';
  propagated.call_chain = [link];

  const boundary = structuredClone(card.boundaries.find((item) => item.kind === 'unresolved_call'));
  boundary.id = 'propagated-boundary';
  boundary.concerns = ['effects'];
  boundary.call_chain = [link];
  const diagnostic = {
    kind: 'unsupported_semantics',
    message: 'In module-local callee helper: unsupported branch.',
    analyses: ['effects'],
    source_span: direct.source_spans[0],
    call_chain: [link]
  };
  const focused = focusCard({
    ...card,
    claims: [direct, propagated],
    boundaries: [boundary],
    diagnostics: [diagnostic]
  }, 'mutation');

  const html = panelHtml(focused, { webview: { cspSource: 'vscode-webview://test' } });

  assert.match(html, /<details class="local-call-evidence">/);
  assert.match(html, /helper\(\.\.\.\).*1 claim, 1 boundary group, 1 diagnostic group/);
  assert.ok(html.indexOf('Attempts to write caller state.') < html.indexOf('Evidence inside local calls'));
  assert.ok(html.indexOf('Evidence inside local calls') < html.indexOf('helper(...) may attempt to write callee state.'));
  assert.match(html, /command:saga\.navigate/);
  assert.match(html, /default-src &#39;none&#39;|default-src 'none'/);
});
