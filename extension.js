const vscode = require('vscode');
const fs = require('node:fs');
const path = require('node:path');
const { loadCard, validateCard, hoverLines } = require('./card');

function cardPath(context) {
  const configured = vscode.workspace.getConfiguration('saga').get('fixturePath');
  return configured ? path.resolve(configured) : path.join(context.extensionPath, 'fixture', 'process_order.card.json');
}

function spanRange(span) {
  return new vscode.Range(span.start_line - 1, span.start_column, span.end_line - 1, span.end_column);
}

function resolveSourcePath(span, context) {
  const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || process.cwd();
  const candidates = [
    path.resolve(root, span.path),
    path.resolve(context.extensionPath, span.path),
    path.resolve(context.extensionPath, 'fixture', path.basename(span.path))
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) || candidates[0];
}

async function navigate(span, context) {
  const uri = vscode.Uri.file(resolveSourcePath(span, context));
  const document = await vscode.workspace.openTextDocument(uri);
  const editor = await vscode.window.showTextDocument(document, { preview: true });
  editor.selection = new vscode.Selection(spanRange(span).start, spanRange(span).end);
  editor.revealRange(spanRange(span), vscode.TextEditorRevealType.InCenter);
}

function panelHtml(card, panel) {
  const esc = (value) => String(value).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const claimHtml = card.claims.map((claim) => {
    const links = claim.source_spans.map((span, index) => `<a href="command:saga.navigate?${encodeURIComponent(JSON.stringify(span))}">source ${index + 1}</a>`).join(' · ');
    const assumptions = claim.assumptions.length ? `<small>Assumptions: ${esc(claim.assumptions.map((a) => a.text).join('; '))}</small>` : '';
    return `<article><h3>${esc(claim.kind.replaceAll('_', ' '))} <em>${esc(claim.evidence.evidence_class)}</em></h3><p>${esc(claim.statement.text)}</p>${assumptions}<p>${links}</p></article>`;
  }).join('');
  const boundaries = card.boundaries.map((boundary) => `<article class="boundary"><h3>${esc(boundary.kind.replaceAll('_', ' '))}</h3><p><strong>${esc(boundary.target.text)}</strong>: ${esc(boundary.reason)}</p><a href="command:saga.navigate?${encodeURIComponent(JSON.stringify(boundary.source_span))}">source</a></article>`).join('');
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${panel.webview.cspSource};"><style>body{font-family:var(--vscode-font-family);padding:0 2em;line-height:1.45}h1{font-size:1.35em}h3{margin-bottom:.25em;text-transform:capitalize}article{border-top:1px solid var(--vscode-panel-border);padding:.7em 0}em{font-size:.75em;font-weight:normal;background:var(--vscode-textBlockQuote-background);padding:.15em .4em}.boundary{border-left:3px solid var(--vscode-editorWarning-foreground);padding-left:1em}small{display:block;color:var(--vscode-descriptionForeground)}</style></head><body><h1>${esc(card.target.name)}</h1><p><code>${esc(card.target.signature)}</code></p><h2>Claims</h2>${claimHtml}<h2>Boundaries</h2>${boundaries || '<p>None</p>'}</body></html>`;
}

function activate(context) {
  const getCard = () => loadCard(cardPath(context));
  const provider = vscode.languages.registerHoverProvider('python', { provideHover(document, position) {
    const word = document.getWordRangeAtPosition(position);
    if (!word || document.getText(word) !== 'process_order') return undefined;
    const card = getCard();
    const errors = validateCard(card);
    if (errors.length) return new vscode.Hover(`Saga fixture error: ${errors.join(', ')}`);
    const markdown = new vscode.MarkdownString(hoverLines(card).join('\n'));
    markdown.isTrusted = { enabledCommands: ['saga.openEvidenceCard', 'saga.navigate'] };
    return new vscode.Hover(markdown);
  }});
  const open = vscode.commands.registerCommand('saga.openEvidenceCard', () => {
    const card = getCard();
    const panel = vscode.window.createWebviewPanel('sagaEvidenceCard', 'Saga Evidence Card', vscode.ViewColumn.Beside, { enableScripts: false, enableCommandUris: ['saga.navigate'] });
    panel.webview.html = panelHtml(card, panel);
  });
  const navigateCommand = vscode.commands.registerCommand('saga.navigate', (span) => navigate(span, context));
  context.subscriptions.push(provider, open, navigateCommand);
}

module.exports = { activate, deactivate: () => {} };
