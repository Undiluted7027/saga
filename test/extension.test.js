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
