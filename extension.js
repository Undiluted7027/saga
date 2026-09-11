const vscode = require('vscode');
const cp = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { targetNameFromLine, validateCard, hoverLines } = require('./card');

const staticCardCache = new Map();

function targetName(document, position) {
  /** Return the function name only when the cursor is on a def statement. */
  const line = document.lineAt(position.line).text;
  return targetNameFromLine(line);
}

function requestCard(context, document, name, force = false) {
  /** Ask the real Python inspector for the current document and target. */
  let stamp = 'missing';
  try {
    const stat = fs.statSync(document.uri.fsPath);
    stamp = `${stat.mtimeMs}:${stat.size}`;
  } catch {}
  const cacheKey = `${document.uri.fsPath}::${name}::${stamp}`;
  if (!force && staticCardCache.has(cacheKey)) return Promise.resolve(staticCardCache.get(cacheKey));
  return new Promise((resolve, reject) => {
    const selector = document.uri.fsPath + '::' + name;
    cp.execFile('python3', ['-m', 'saga.cli', 'inspect', selector, '--format', 'json'], { cwd: context.extensionPath, maxBuffer: 1024 * 1024 }, (error, stdout, stderr) => {
      if (!stdout) return reject(new Error(stderr.trim() || error?.message || 'Saga inspection failed'));
      try {
        const card = JSON.parse(stdout);
        staticCardCache.set(cacheKey, card);
        resolve(card);
      } catch (parseError) { reject(parseError); }
    });
  });
}

function requestTestCard(context, document, name) {
  /** Run the opt-in pytest trace command using the repository's test extra. */
  return new Promise((resolve, reject) => {
    const selector = document.uri.fsPath + '::' + name;
    const pytestArgs = vscode.workspace.getConfiguration('saga').get('pytestArgs', []);
    const args = ['run', '--extra', 'test', 'python', '-m', 'saga.cli', 'test', selector, '--format', 'json', '--', ...pytestArgs];
    cp.execFile('uv', args, { cwd: context.extensionPath, maxBuffer: 2 * 1024 * 1024 }, (error, stdout, stderr) => {
      if (!stdout) return reject(new Error(stderr.trim() || error?.message || 'Saga test run failed'));
      try { resolve(JSON.parse(stdout)); } catch (parseError) { reject(parseError); }
    });
  });
}

function spanRange(span) {
  /** Convert Saga's one-based inclusive-looking line fields to VS Code ranges. */
  return new vscode.Range(span.start_line - 1, span.start_column, span.end_line - 1, span.end_column);
}

function resolveSourcePath(span, context) {
  /** Resolve both repository-relative and workspace-relative evidence paths. */
  const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || process.cwd();
  const candidates = [
    path.resolve(root, span.path),
    path.resolve(context.extensionPath, span.path),
    path.resolve(context.extensionPath, 'fixture', path.basename(span.path))
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || candidates[0];
}

async function navigate(span, context) {
  /** Open, select, and reveal the source span attached to a claim. */
  const uri = vscode.Uri.file(resolveSourcePath(span, context));
  const document = await vscode.workspace.openTextDocument(uri);
  const existing = vscode.window.visibleTextEditors.find((editor) => editor.document.uri.toString() === document.uri.toString());
  const editor = await vscode.window.showTextDocument(document, { viewColumn: existing?.viewColumn || vscode.ViewColumn.One, preview: false });
  const range = spanRange(span);
  editor.selection = new vscode.Selection(range.start, range.end);
  editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
}

function panelHtml(card, panel) {
  /** Render the shared structured result as a navigable webview document. */
  const esc = (value) => String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const renderClaims = (claims) => claims.map((claim) => {
    const links = claim.source_spans.map((span, index) => `<a href="command:saga.navigate?${encodeURIComponent(JSON.stringify(span))}">source ${index + 1}</a>`).join(' · ');
    const assumptions = claim.assumptions.length ? `<small>Assumptions: ${esc(claim.assumptions.map((a) => a.text).join('; '))}</small>` : '';
    const label = claim.statement.type || claim.kind;
    const condition = 'condition' in claim.statement ? `<details><summary>Structured condition</summary><pre>${esc(JSON.stringify(claim.statement.condition, null, 2))}</pre></details>` : '';
    const dependencies = claim.statement.dependencies ? `<details><summary>Dependencies</summary><ul>${claim.statement.dependencies.map((dependency) => { const reads = dependency.reads?.length ? ` · reads ${dependency.reads.join(', ')}` : ''; const calls = dependency.calls?.length ? ` · calls ${dependency.calls.map((call) => call.text).join(', ')}` : ''; return `<li>${esc(dependency.kind)} · line ${dependency.source_span.start_line}${esc(reads)}${esc(calls)}</li>`; }).join('')}</ul></details>` : '';
    const detail = claim.evidence.evidence_class === 'observed' ? `<details><summary>Observation details</summary><pre>${esc(JSON.stringify(claim.evidence.detail, null, 2))}</pre></details>` : '';
    return `<article><h3>${esc(label.replaceAll('_', ' '))} <em>${esc(claim.evidence.evidence_class)}</em></h3><p>${esc(claim.statement.text)}</p>${condition}${dependencies}${detail}${assumptions}<p>${links}</p></article>`;
  }).join('');
  const derivedClaims = renderClaims(card.claims.filter((claim) => claim.evidence.evidence_class !== 'observed'));
  const observedClaims = renderClaims(card.claims.filter((claim) => claim.evidence.evidence_class === 'observed'));
  const renderBoundary = (boundary) => `<article class="boundary"><h3>${esc(boundary.kind.replaceAll('_', ' '))}</h3><p><strong>${esc(boundary.target.text)}</strong>: ${esc(boundary.reason)}</p><a href="command:saga.navigate?${encodeURIComponent(JSON.stringify(boundary.source_span))}">source</a></article>`;
  const importantBoundaries = card.boundaries.filter((boundary) => boundary.category !== 'routine');
  const routineBoundaries = card.boundaries.filter((boundary) => boundary.category === 'routine');
  const boundaries = importantBoundaries.map(renderBoundary).join('') + (routineBoundaries.length ? `<details><summary>${routineBoundaries.length} routine unresolved calls</summary>${routineBoundaries.map(renderBoundary).join('')}</details>` : '');
  const diagnostics = card.diagnostics.map((diagnostic) => {
    const source = diagnostic.source_span ? ` <a href="command:saga.navigate?${encodeURIComponent(JSON.stringify(diagnostic.source_span))}">source</a>` : '';
    return `<article class="diagnostic"><h3>${esc(diagnostic.kind)}</h3><p>${esc(diagnostic.message)}${source}</p></article>`;
  }).join('');
  const target = encodeURIComponent(JSON.stringify({ path: card.target.path, name: card.target.name }));
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${panel.webview.cspSource};"><style>body{font-family:var(--vscode-font-family);padding:0 2em;line-height:1.45}h1{font-size:1.35em}h3{margin-bottom:.25em;text-transform:capitalize}article{border-top:1px solid var(--vscode-panel-border);padding:.7em 0}em{font-size:.75em;font-weight:normal;background:var(--vscode-textBlockQuote-background);padding:.15em .4em}.boundary{border-left:3px solid var(--vscode-editorWarning-foreground);padding-left:1em}.diagnostic{border-left:3px solid var(--vscode-editorError-foreground);padding-left:1em}small{display:block;color:var(--vscode-descriptionForeground)}pre{white-space:pre-wrap}</style></head><body><h1>${esc(card.target.name)}</h1><p><code>${esc(card.target.signature)}</code></p><p><a href="command:saga.runTests?${target}">Run tests for this function</a></p><h2>Derived claims</h2>${derivedClaims || '<p>None</p>'}<h2>Observed claims</h2>${observedClaims || '<p>None</p>'}<h2>Boundaries</h2>${boundaries || '<p>None</p>'}<h2>Diagnostics</h2>${diagnostics || '<p>None</p>'}</body></html>`;
}

const evidencePanels = new Map();

function showCardPanel(context, card, document, name) {
  /** Keep an evidence panel current after the source file is saved. */
  const key = `${document.uri.toString()}::${name}`;
  const existing = evidencePanels.get(key);
  if (existing) {
    existing.panel.reveal(vscode.ViewColumn.Beside, false);
    existing.render(card);
    return existing.panel;
  }
  const panel = vscode.window.createWebviewPanel('sagaEvidenceCard', 'Saga Evidence Card', vscode.ViewColumn.Beside, { enableScripts: false, enableCommandUris: ['saga.navigate', 'saga.runTests'] });
  let refreshTimer;
  const render = (nextCard) => { panel.webview.html = panelHtml(nextCard, panel); };
  const refresh = async () => {
    try {
      render(await requestCard(context, document, name, true));
    } catch (error) {
      // Keep the last valid card visible when a transient edit is not parseable.
      void vscode.window.showWarningMessage('Saga could not refresh the evidence card: ' + error.message);
    }
  };
  const saveSubscription = vscode.workspace.onDidSaveTextDocument((savedDocument) => {
    if (savedDocument.uri.toString() !== document.uri.toString()) return;
    staticCardCache.clear();
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, 100);
  });
  panel.onDidDispose(() => {
    evidencePanels.delete(key);
    saveSubscription.dispose();
    clearTimeout(refreshTimer);
  });
  evidencePanels.set(key, { panel, render });
  render(card);
  return panel;
}

function activate(context) {
  /** Register the fixture-era hover, real inspection, panel, and navigation flows. */
  const provider = vscode.languages.registerHoverProvider('python', { provideHover(document, position) {
    const name = targetName(document, position);
    if (!name) return undefined;
    return requestCard(context, document, name).then((card) => {
      const errors = validateCard(card);
      if (errors.length) return new vscode.Hover('Saga result error: ' + errors.join(', '));
      const markdown = new vscode.MarkdownString(hoverLines(card).join('\n'));
      markdown.isTrusted = { enabledCommands: ['saga.openEvidenceCard', 'saga.navigate'] };
      return new vscode.Hover(markdown);
    }).catch((error) => new vscode.Hover('Saga could not inspect this function: ' + error.message));
  }});
  const open = vscode.commands.registerCommand('saga.openEvidenceCard', async (request) => {
    const activeEditor = vscode.window.activeTextEditor;
    const document = request?.path
      ? await vscode.workspace.openTextDocument(vscode.Uri.file(request.path))
      : activeEditor?.document;
    const name = request?.name || (activeEditor && targetName(activeEditor.document, activeEditor.selection.active));
    if (!document || !name) return vscode.window.showErrorMessage('Open the evidence card from a function hover or place the cursor on a module-level function definition first.');
    let card;
    try { card = await requestCard(context, document, name); }
    catch (error) { return vscode.window.showErrorMessage('Saga inspection failed: ' + error.message); }
    showCardPanel(context, card, document, name);
  });
  const runTests = vscode.commands.registerCommand('saga.runTests', async (request) => {
    const activeEditor = vscode.window.activeTextEditor;
    const document = request?.path
      ? await vscode.workspace.openTextDocument(vscode.Uri.file(request.path))
      : activeEditor?.document;
    const name = request?.name || (activeEditor && targetName(activeEditor.document, activeEditor.selection.active));
    if (!document || !name) return vscode.window.showErrorMessage('Place the cursor on a module-level function definition first.');
    let card;
    try { card = await requestTestCard(context, document, name); }
    catch (error) { return vscode.window.showErrorMessage('Saga test run failed: ' + error.message); }
    showCardPanel(context, card, document, name);
  });
  const navigateCommand = vscode.commands.registerCommand('saga.navigate', (span) => navigate(span, context));
  context.subscriptions.push(provider, open, runTests, navigateCommand);
}

module.exports = { activate, deactivate: () => {} };
