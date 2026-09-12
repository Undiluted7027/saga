const vscode = require('vscode');
const cp = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { targetNameFromLine, validateCard, claimPresentation, focusCard, viewPresentation, boundaryGroups, diagnosticGroups, hoverLines } = require('./card');

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
  } catch { }
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
  const view = viewPresentation(card);
  const renderCallChain = (chain) => chain?.length ? `<details><summary>Local call chain</summary><ol>${chain.map((link) => { const bindings = link.argument_bindings.length ? ` (${link.argument_bindings.map((item) => `${item.parameter} = ${item.argument}`).join(', ')})` : ''; return `<li>${esc(link.caller)} → ${esc(link.callee)}${esc(bindings)} · <a href="command:saga.navigate?${encodeURIComponent(JSON.stringify(link.call_site))}">call</a> · <a href="command:saga.navigate?${encodeURIComponent(JSON.stringify(link.callee_span))}">callee</a></li>`; }).join('')}</ol></details>` : '';
  const renderClaims = (claims) => claims.map((claim) => {
    const view = claimPresentation(claim);
    const links = view.sourceSpans.map((span, index) => `<a href="command:saga.navigate?${encodeURIComponent(JSON.stringify(span))}">source ${index + 1}</a>`).join(' · ');
    const assumptions = view.assumptions.length ? `<small>Assumptions: ${esc(view.assumptions.join('; '))}</small>` : '';
    const evidence = `<small>Method: ${esc(view.method)}${view.boundaryIds.length ? ` · Limited by: ${esc(view.boundaryIds.join(', '))}` : ''}</small>`;
    const callChain = renderCallChain(view.callChain);
    const label = claim.statement.type || claim.kind;
    const sourceExpression = view.sourceText ? `<p>Source syntax: <code>${esc(view.sourceText)}</code></p>` : '';
    const conditionSource = view.conditionSourceText ? `<p>Condition syntax: <code>${esc(view.conditionSourceText)}</code></p>` : '';
    const handlers = view.handlerSpans.length ? `<p>Handlers checked: ${view.handlerSpans.map((span) => `<a href="command:saga.navigate?${encodeURIComponent(JSON.stringify(span))}">${esc(`${span.path}:${span.start_line}`)}</a>`).join(' · ')}</p>` : '';
    const condition = 'condition' in claim.statement ? `<details><summary>Structured condition</summary><pre>${esc(JSON.stringify(view.condition, null, 2))}</pre></details>` : '';
    const dependencies = view.dependencies.length ? `<details><summary>Why this return may have this value</summary><ul>${view.dependencies.map((dependency) => { const verb = dependency.kind === 'weak_definition' ? 'may change' : 'defines'; const writes = dependency.names?.length ? `${verb} ${dependency.names.join(', ')}` : ''; const reads = dependency.reads?.length ? `reads ${dependency.reads.join(', ')}` : ''; const calls = dependency.calls?.length ? `calls ${dependency.calls.map((call) => call.text).join(', ')}` : ''; const facts = [writes, reads, calls].filter(Boolean).join('; ') || dependency.kind.replaceAll('_', ' '); return `<li>Line ${dependency.source_span.start_line}: ${esc(facts)}</li>`; }).join('')}</ul></details>` : '';
    const localDependencies = view.localCallDependencies.length ? `<details><summary>Inputs carried through local calls</summary><ul>${view.localCallDependencies.map((dependency) => { const inputs = dependency.caller_inputs.length ? `; caller inputs: ${dependency.caller_inputs.join(', ')}` : ''; return `<li><code>${esc(dependency.callee_parameter)}</code> = <code>${esc(dependency.caller_argument)}</code> (${esc(dependency.binding_origin + inputs)})${renderCallChain(dependency.call_chain)}</li>`; }).join('')}</ul></details>` : '';
    const detail = view.evidenceClass === 'observed' ? `<details><summary>Observation details</summary><pre>${esc(JSON.stringify(claim.evidence.detail, null, 2))}</pre></details>` : '';
    return `<article><h3>${esc(label.replaceAll('_', ' '))} <em>${esc(view.evidenceClass)}</em></h3><p>${esc(view.summary)}</p>${sourceExpression}${conditionSource}${handlers}${condition}${dependencies}${localDependencies}${callChain}${detail}${evidence}${assumptions}<p>${links}</p></article>`;
  }).join('');
  const derivedClaims = renderClaims(card.claims.filter((claim) => claim.evidence.evidence_class !== 'observed'));
  const observedClaims = renderClaims(card.claims.filter((claim) => claim.evidence.evidence_class === 'observed'));
  const renderBoundaryGroup = (group) => {
    const sites = group.occurrences.map((occurrence) => `<li><a href="command:saga.navigate?${encodeURIComponent(JSON.stringify(occurrence.sourceSpan))}">${esc(`${occurrence.sourceSpan.path}:${occurrence.sourceSpan.start_line}`)}</a>${renderCallChain(occurrence.callChain)}</li>`).join('');
    const limitedClaims = group.claimKinds.length ? `<small>Limits: ${esc(group.claimKinds.join(', '))}</small>` : '';
    const limitedBy = group.limitingBoundaryIds.length ? `<small>Limited by: ${esc(group.limitingBoundaryIds.join(', '))}</small>` : '';
    const isCall = ['routine_call', 'module_local', 'external_or_unresolved_call'].includes(group.boundaryClass);
    const siteLabel = `${isCall ? 'call' : 'source'} site${group.count === 1 ? '' : 's'}`;
    const locations = group.count === 1 ? `<ol>${sites}</ol>` : `<details><summary>${group.count} source locations</summary><ol>${sites}</ol></details>`;
    return `<article class="boundary"><h3>${esc(group.boundaryClass.replaceAll('_', ' '))} <em>${esc(group.kind.replaceAll('_', ' '))}</em></h3><p><strong>${esc(group.target)}</strong> · ${group.count} ${siteLabel}</p><p>${esc(group.reason)}</p>${limitedClaims}${limitedBy}${locations}</article>`;
  };
  const groups = boundaryGroups(card);
  const importantBoundaries = groups.filter((group) => group.category !== 'routine');
  const routineBoundaries = groups.filter((group) => group.category === 'routine');
  const routineSites = routineBoundaries.reduce((total, group) => total + group.count, 0);
  const routineGroupLabel = routineBoundaries.length === 1 ? 'group' : 'groups';
  const routineSummary = routineBoundaries.length ? `<details><summary>${routineSites} routine unresolved call sites in ${routineBoundaries.length} ${routineGroupLabel}</summary>${routineBoundaries.map(renderBoundaryGroup).join('')}</details>` : '';
  const boundaries = importantBoundaries.map(renderBoundaryGroup).join('') + routineSummary;
  const diagnostics = diagnosticGroups(card).map((group) => {
    const sites = group.occurrences.map((occurrence) => { const source = occurrence.sourceSpan ? `<a href="command:saga.navigate?${encodeURIComponent(JSON.stringify(occurrence.sourceSpan))}">${esc(`${occurrence.sourceSpan.path}:${occurrence.sourceSpan.start_line}`)}</a>` : 'No source location'; return `<li>${source}${renderCallChain(occurrence.callChain)}</li>`; }).join('');
    const count = `${group.reportCount} report${group.reportCount === 1 ? '' : 's'} at ${group.siteCount} source site${group.siteCount === 1 ? '' : 's'}`;
    const locations = group.reportCount > 1 ? `<details><summary>${esc(count)}</summary><ol>${sites}</ol></details>` : `<ol>${sites}</ol>`;
    return `<article class="diagnostic"><h3>${esc(group.kind)}</h3><p>${esc(group.message)}</p>${locations}</article>`;
  }).join('');
  const observationStatus = card.observation_status;
  const observationSummary = observationStatus
    ? `<article class="observation-status"><h3>${esc(observationStatus.state.replaceAll('_', ' '))}</h3><p>${esc(observationStatus.message)}</p><small>${observationStatus.execution_count} executions · ${observationStatus.returned_executions} returned · ${observationStatus.raised_executions} raised · reason: ${esc(observationStatus.reason.replaceAll('_', ' '))}</small>${observationStatus.tests.length ? `<small>Tests: ${esc(observationStatus.tests.join(', '))}</small>` : ''}${Object.keys(observationStatus.environment).length ? `<small>Environment: ${esc(Object.entries(observationStatus.environment).map(([name, value]) => `${name}: ${value}`).join(', '))}</small>` : ''}</article>`
    : '<p>No completed test trace is attached to this card.</p>';
  const request = (name) => encodeURIComponent(JSON.stringify({ path: card.target.path, name: card.target.name, view: name }));
  const navigation = `<nav><a href="command:saga.openEvidenceCard?${request('full')}">Full card</a> · <a href="command:saga.openEvidenceCard?${request('return')}">Return</a> · <a href="command:saga.openEvidenceCard?${request('mutation')}">Mutation</a> · <a href="command:saga.openEvidenceCard?${request('failure')}">Failure</a> · <a href="command:saga.openEvidenceCard?${request('boundary')}">Boundaries</a></nav>`;
  const empty = view.empty ? `<p class="empty">${esc(view.emptyMessage)}</p>` : '';
  const fullContent = `<h2>Derived claims</h2>${derivedClaims || '<p>None</p>'}<h2>Observed claims</h2>${observedClaims}${observationSummary}<h2>Boundaries</h2>${boundaries || '<p>None</p>'}<h2>Diagnostics</h2>${diagnostics || '<p>None</p>'}`;
  const focusedClaims = view.name === 'boundary' ? '' : `<h2>${esc(view.label)}</h2>${empty || renderClaims(card.claims)}`;
  const focusedBoundaries = `<h2>${view.name === 'boundary' ? 'Analysis boundaries' : 'Related boundaries'}</h2>${view.name === 'boundary' && empty ? empty : boundaries || '<p>None limit this evidence.</p>'}`;
  const focusedDiagnostics = diagnostics ? `<h2>Target diagnostics</h2>${diagnostics}` : '';
  const focusedObservationStatus = observationStatus ? `<h2>Test observation status</h2>${observationSummary}` : '';
  const content = view.name === 'full' ? fullContent : focusedClaims + focusedBoundaries + focusedObservationStatus + focusedDiagnostics;
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${panel.webview.cspSource};"><style>body{font-family:var(--vscode-font-family);padding:0 2em;line-height:1.45}h1{font-size:1.35em}h3{margin-bottom:.25em;text-transform:capitalize}nav{margin:.8em 0}.empty{border-left:3px solid var(--vscode-editorWarning-foreground);padding:.6em 1em}article{border-top:1px solid var(--vscode-panel-border);padding:.7em 0}em{font-size:.75em;font-weight:normal;background:var(--vscode-textBlockQuote-background);padding:.15em .4em}.boundary{border-left:3px solid var(--vscode-editorWarning-foreground);padding-left:1em}.diagnostic{border-left:3px solid var(--vscode-editorError-foreground);padding-left:1em}.observation-status{border-left:3px solid var(--vscode-editorInfo-foreground);padding-left:1em}small{display:block;color:var(--vscode-descriptionForeground)}pre{white-space:pre-wrap}</style></head><body><h1>${esc(card.target.name)} · ${esc(view.label)}</h1><p><code>${esc(card.target.signature)}</code></p>${navigation}<p><a href="command:saga.runTests?${request(view.name)}">Run tests for this function</a></p>${content}</body></html>`;
}

const evidencePanels = new Map();

function showCardPanel(context, card, document, name, view = 'full') {
  /** Keep an evidence panel current after the source file is saved. */
  const key = `${document.uri.toString()}::${name}`;
  const existing = evidencePanels.get(key);
  if (existing) {
    existing.panel.reveal(vscode.ViewColumn.Beside, false);
    existing.setView(view);
    existing.render(card);
    return existing.panel;
  }
  const panel = vscode.window.createWebviewPanel('sagaEvidenceCard', 'Saga Evidence Card', vscode.ViewColumn.Beside, { enableScripts: false, enableCommandUris: ['saga.navigate', 'saga.runTests', 'saga.openEvidenceCard'] });
  let refreshTimer;
  let activeView = view;
  let latestCard = card;
  const render = (nextCard) => {
    latestCard = nextCard;
    panel.title = `Saga: ${viewPresentation(focusCard(nextCard, activeView)).label}`;
    panel.webview.html = panelHtml(focusCard(nextCard, activeView), panel);
  };
  const setView = (nextView) => {
    activeView = nextView;
  };
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
  evidencePanels.set(key, { panel, render, setView });
  render(card);
  return panel;
}

function activate(context) {
  /** Register the fixture-era hover, real inspection, panel, and navigation flows. */
  const provider = vscode.languages.registerHoverProvider('python', {
    provideHover(document, position) {
      const name = targetName(document, position);
      if (!name) return undefined;
      return requestCard(context, document, name).then((card) => {
        const errors = validateCard(card);
        if (errors.length) return new vscode.Hover('Saga result error: ' + errors.join(', '));
        const markdown = new vscode.MarkdownString(hoverLines(card).join('\n'));
        markdown.isTrusted = { enabledCommands: ['saga.openEvidenceCard', 'saga.navigate'] };
        return new vscode.Hover(markdown);
      }).catch((error) => new vscode.Hover('Saga could not inspect this function: ' + error.message));
    }
  });
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
    showCardPanel(context, card, document, name, request?.view || 'full');
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
    showCardPanel(context, card, document, name, request?.view || 'full');
  });
  const openReturn = vscode.commands.registerCommand(
    'saga.openReturnEvidence',
    () => vscode.commands.executeCommand('saga.openEvidenceCard', { view: 'return' })
  );
  const openMutation = vscode.commands.registerCommand(
    'saga.openMutationEvidence',
    () => vscode.commands.executeCommand('saga.openEvidenceCard', { view: 'mutation' })
  );
  const openFailure = vscode.commands.registerCommand(
    'saga.openFailureEvidence',
    () => vscode.commands.executeCommand('saga.openEvidenceCard', { view: 'failure' })
  );
  const openBoundaries = vscode.commands.registerCommand(
    'saga.openBoundaryEvidence',
    () => vscode.commands.executeCommand('saga.openEvidenceCard', { view: 'boundary' })
  );
  const navigateCommand = vscode.commands.registerCommand('saga.navigate', (span) => navigate(span, context));
  context.subscriptions.push(
    provider,
    open,
    openReturn,
    openMutation,
    openFailure,
    openBoundaries,
    runTests,
    navigateCommand
  );
}

module.exports = { activate, deactivate: () => { } };
