const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { panelHtml } = require('../webview');

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

  const html = panelHtml(focused, { webview: { cspSource: 'vscode-webview://test' } }, 'vscode-webview://test/media/evidence-card.css');

  assert.match(html, /<span class="eyebrow">Return path 1<\/span><code>result<\/code>/);
  assert.match(html, /<strong>When<\/strong> enabled is truthy/);
  assert.match(html, /<details class="return-sites"><summary><span>Dependency evidence<\/span><span class="count">9 sites · 2 groups<\/span><\/summary>/);
  assert.match(html, /<span>definition<\/span><span class="count">8 sites<\/span>/);
  assert.match(html, /normalize\(\.\.\.\)/);
  assert.match(html, /Additional claim sources/);
  assert.match(html, /Source 1 · line 30/);
  assert.match(html, /aria-current="page"[^>]*>Returns<\/a>/);
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

  const html = panelHtml(focused, { webview: { cspSource: 'vscode-webview://test' } }, 'vscode-webview://test/media/evidence-card.css');

  assert.match(html, /<details class="local-call-evidence">/);
  assert.match(html, /1 recorded write site across 1 target/);
  assert.match(html, /1 more claim is kept inside local-call evidence/);
  assert.match(html, /<code>helper\(\.\.\.\)<\/code><small>1 claim · 1 boundary group · 1 diagnostic group<\/small>/);
  assert.ok(html.indexOf('Attempts to write caller state.') < html.indexOf('Evidence inside local calls'));
  assert.ok(html.indexOf('Evidence inside local calls') < html.indexOf('helper(...) may attempt to write callee state.'));
  assert.match(html, /command:saga\.navigate/);
  assert.match(html, /default-src 'none'; style-src vscode-webview:\/\/test;/);
  assert.match(html, /<link rel="stylesheet" href="vscode-webview:\/\/test\/media\/evidence-card\.css">/);
  assert.doesNotMatch(html, /<style|<script/);
  assert.equal((html.match(/Local call chain/g) || []).length, 2);
});

test('overview separates supported facts, observations, limits, and analyzer reports', () => {
  const card = loadCard();
  const html = panelHtml(card, { webview: { cspSource: 'vscode-webview://test' } }, 'style.css');

  assert.match(html, /<html lang="en">/);
  assert.match(html, /<nav class="view-tabs" aria-label="Evidence views">/);
  assert.match(html, /aria-current="page"[^>]*>Overview<\/a>/);
  assert.match(html, /What Saga derived/);
  assert.match(html, /class="overview-claim-group"/);
  assert.match(html, /Which values and branches can feed the result\?/);
  assert.match(html, /What state or external system can this function touch\?/);
  assert.match(html, /Which inputs are rejected, and which exceptions can escape\?/);
  assert.match(html, /What tests observed/);
  assert.match(html, /Where Saga stopped/);
  assert.match(html, /What needs attention/);
  assert.match(html, /Evidence classes stay separate/);
  assert.match(html, /role="group" aria-label="Source evidence"/);
});

test('focused metrics say that their counts belong to the current view', () => {
  const card = focusCard(loadCard(), 'failure');
  const html = panelHtml(card, { webview: { cspSource: 'vscode-webview://test' } }, 'style.css');

  assert.match(html, /<dt>Claims in view<\/dt>/);
  assert.match(html, /<dt>Boundaries in view<\/dt>/);
  assert.match(html, /<dt>Diagnostics in view<\/dt>/);
  assert.match(html, /<dt>Local calls<\/dt>/);
  assert.doesNotMatch(html, /<dt>Derived<\/dt>/);
});

test('focused webview puts the answer before claims and limits', () => {
  const card = focusCard(loadCard(), 'mutation');
  const html = panelHtml(card, { webview: { cspSource: 'vscode-webview://test' } }, 'style.css');

  const answer = html.indexOf('Direct answer');
  const claims = html.indexOf('Supporting claims');
  const limits = html.indexOf('Limits on this answer');
  assert.ok(answer >= 0);
  assert.ok(answer < claims);
  assert.ok(claims < limits);
  assert.match(html, /Unresolved calls remain separate because Saga cannot classify their effects/);
});

test('focused webview groups repeated claims and retains every record', () => {
  const card = loadCard();
  const write = structuredClone(card.claims.find((claim) => claim.kind === 'attempted_write'));
  const repeated = structuredClone(write);
  repeated.id = 'repeat-write';
  repeated.source_spans[0].start_line += 1;
  const focused = focusCard({ ...card, claims: [write, repeated], boundaries: [], diagnostics: [] }, 'mutation');

  const html = panelHtml(focused, { webview: { cspSource: 'vscode-webview://test' } }, 'style.css');

  assert.match(html, /<details class="claim-cluster">/);
  assert.match(html, /2 evidence records/);
  assert.equal((html.match(/class="claim claim--attempted_write"/g) || []).length, 2);
});

test('focused webview renders a linked limit before other direct limits', () => {
  const card = loadCard();
  const write = structuredClone(card.claims.find((claim) => claim.kind === 'attempted_write'));
  const linked = structuredClone(card.boundaries.find((item) => item.kind === 'unresolved_call'));
  linked.id = 'linked-limit';
  linked.target.text = 'gateway(...)';
  linked.concerns = ['effects'];
  const other = structuredClone(linked);
  other.id = 'other-limit';
  other.target.text = 'notify(...)';
  write.boundary_ids = [linked.id];
  const focused = focusCard({ ...card, claims: [write], boundaries: [other, linked], diagnostics: [] }, 'mutation');

  const html = panelHtml(focused, { webview: { cspSource: 'vscode-webview://test' } }, 'style.css');

  assert.ok(html.indexOf('gateway(...)') < html.indexOf('Other direct limits'));
  assert.ok(html.indexOf('Other direct limits') < html.indexOf('notify(...)'));
  assert.match(html, /Directly limits the writes and effects answer/);
});

test('webview escapes analyzed source and uses an external theme-aware stylesheet', () => {
  const card = loadCard();
  card.target.name = '<img src=x onerror=alert(1)>';
  card.target.signature = 'def inspect(value="<&")';
  const html = panelHtml(card, { webview: { cspSource: 'vscode-webview://test' } }, 'style.css');
  const css = fs.readFileSync(path.join(__dirname, '..', 'media', 'evidence-card.css'), 'utf8');

  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(html, /value=&quot;&lt;&amp;&quot;/);
  assert.match(css, /--vscode-editor-background/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /@media \(forced-colors: active\)/);
  assert.match(css, /@media \(max-width: 680px\)/);
});

test('extension loads the stylesheet as a local webview resource without enabling scripts', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'extension.js'), 'utf8');
  assert.match(source, /localResourceRoots: \[mediaRoot\]/);
  assert.match(source, /asWebviewUri\(vscode\.Uri\.joinPath\(mediaRoot, 'evidence-card\.css'\)\)/);
  assert.match(source, /enableScripts: false/);
});
