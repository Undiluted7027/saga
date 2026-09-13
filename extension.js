const vscode = require('vscode');
const cp = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { targetSelectorAt, validateCard, focusCard, viewPresentation, hoverLines } = require('./card');
const { panelHtml } = require('./webview');

const staticCardCache = new Map();

function targetName(document, position) {
  /** Return the enclosing module function or class-qualified method selector. */
  const lines = Array.from(
    { length: document.lineCount },
    (_, index) => document.lineAt(index).text
  );
  return targetSelectorAt(lines, position.line);
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

const evidencePanels = new Map();

function showCardPanel(context, card, document, name, view = 'full') {
  /** Keep one secure, source-linked evidence workspace current after saves. */
  const key = `${document.uri.toString()}::${name}`;
  const existing = evidencePanels.get(key);
  if (existing) {
    existing.panel.reveal(vscode.ViewColumn.Beside, false);
    existing.setView(view);
    existing.render(card);
    return existing.panel;
  }
  const mediaRoot = vscode.Uri.joinPath(context.extensionUri, 'media');
  const panel = vscode.window.createWebviewPanel(
    'sagaEvidenceCard',
    'Saga Evidence Card',
    vscode.ViewColumn.Beside,
    {
      enableScripts: false,
      enableCommandUris: ['saga.navigate', 'saga.runTests', 'saga.openEvidenceCard'],
      localResourceRoots: [mediaRoot]
    }
  );
  const styleUri = panel.webview.asWebviewUri(vscode.Uri.joinPath(mediaRoot, 'evidence-card.css'));
  let refreshTimer;
  let activeView = view;
  const render = (nextCard) => {
    const focused = focusCard(nextCard, activeView);
    panel.title = `Saga: ${viewPresentation(focused).label}`;
    panel.webview.html = panelHtml(focused, panel, styleUri);
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
  /** Register hover, inspection, evidence views, test runs, and navigation. */
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
    if (!document || !name) return vscode.window.showErrorMessage('Open the evidence card from a function hover or place the cursor inside a module function or method first.');
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
    if (!document || !name) return vscode.window.showErrorMessage('Place the cursor inside a module function or method first.');
    let card;
    try { card = await requestTestCard(context, document, name); }
    catch (error) { return vscode.window.showErrorMessage('Saga test run failed: ' + error.message); }
    showCardPanel(context, card, document, name, request?.view || 'full');
  });
  const openReturn = vscode.commands.registerCommand('saga.openReturnEvidence', () => vscode.commands.executeCommand('saga.openEvidenceCard', { view: 'return' }));
  const openMutation = vscode.commands.registerCommand('saga.openMutationEvidence', () => vscode.commands.executeCommand('saga.openEvidenceCard', { view: 'mutation' }));
  const openFailure = vscode.commands.registerCommand('saga.openFailureEvidence', () => vscode.commands.executeCommand('saga.openEvidenceCard', { view: 'failure' }));
  const openBoundaries = vscode.commands.registerCommand('saga.openBoundaryEvidence', () => vscode.commands.executeCommand('saga.openEvidenceCard', { view: 'boundary' }));
  const navigateCommand = vscode.commands.registerCommand('saga.navigate', (span) => navigate(span, context));
  context.subscriptions.push(provider, open, openReturn, openMutation, openFailure, openBoundaries, runTests, navigateCommand);
}

module.exports = { activate, deactivate: () => { }, panelHtml };
