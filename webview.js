const {
  boundaryGroups,
  claimPresentation,
  diagnosticGroups,
  localCallEvidence,
  observationPresentation,
  returnPathPresentation,
  viewPresentation
} = require('./card');

const VIEW_COPY = {
  full: 'Start with the claim. Open its evidence when you need to verify it.',
  return: 'Which values and branches can feed each return path?',
  mutation: 'What state can this function attempt to change?',
  failure: 'Which inputs are rejected, and which explicit exceptions can escape?',
  boundary: 'Where did Saga stop, and what remains unresolved?'
};

const escapeHtml = (value) => String(value).replace(
  /[&<>"']/g,
  (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]
);
const plural = (count, singular, pluralForm = `${singular}s`) => `${count} ${count === 1 ? singular : pluralForm}`;
const safeClass = (value) => String(value).replace(/[^a-z0-9_-]/gi, '-').toLowerCase();

function commandLink(command, argument, label, className = '', attributes = '') {
  const href = `command:${command}?${encodeURIComponent(JSON.stringify(argument))}`;
  return `<a${className ? ` class="${className}"` : ''}${attributes ? ` ${attributes}` : ''} href="${href}">${escapeHtml(label)}</a>`;
}

function panelHtml(card, panel, styleUri = 'evidence-card.css') {
  /** Render a static, keyboard-native evidence workspace. */
  const view = viewPresentation(card);
  const partition = view.name === 'full' ? undefined : localCallEvidence(card);
  const request = (name) => ({ path: card.target.path, name: card.target.name, view: name });
  const sourceLink = (span, label) => commandLink('saga.navigate', span, label, 'source-link');

  const renderCallChain = (chain) => {
    if (!chain?.length) return '';
    const steps = chain.map((link) => {
      const bindings = link.argument_bindings?.length
        ? `<p class="detail-copy"><strong>Arguments</strong> ${escapeHtml(link.argument_bindings.map((item) => `${item.parameter} = ${item.argument}`).join(', '))}</p>`
        : '';
      return `<li><span class="call-route"><code>${escapeHtml(link.caller)}</code><span aria-hidden="true">→</span><code>${escapeHtml(link.callee)}</code></span><span class="link-row">${sourceLink(link.call_site, 'Open call')} ${sourceLink(link.callee_span, 'Open callee')}</span>${bindings}</li>`;
    }).join('');
    return `<details class="subdetail"><summary>Local call chain · ${plural(chain.length, 'hop')}</summary><ol class="evidence-list">${steps}</ol></details>`;
  };

  const dependencyFacts = (dependency) => {
    const verb = dependency.kind === 'weak_definition' ? 'may change' : 'defines';
    const names = dependency.names?.length ? `${verb} ${dependency.names.join(', ')}` : '';
    const reads = dependency.reads?.length ? `reads ${dependency.reads.join(', ')}` : '';
    const calls = dependency.calls?.length ? `calls ${dependency.calls.map((call) => call.text).join(', ')}` : '';
    return [names, reads, calls].filter(Boolean).join('; ') || dependency.kind.replaceAll('_', ' ');
  };

  const renderSources = (spans) => spans.length
    ? `<div class="source-row" role="group" aria-label="Source evidence">${spans.map((span, index) => sourceLink(span, `Source ${index + 1} · line ${span.start_line}`)).join('')}</div>`
    : '';

  const renderReturnDependencies = (returnPath, dependencies) => {
    if (!dependencies.length) return '';
    if (!returnPath?.compact) {
      const entries = dependencies.map((dependency) => `<li><span class="line-label">Line ${dependency.source_span.start_line}</span><span>${escapeHtml(dependencyFacts(dependency))}</span></li>`).join('');
      return `<details class="subdetail"><summary>Why this value · ${plural(dependencies.length, 'site')}</summary><ul class="evidence-list">${entries}</ul></details>`;
    }
    const groups = returnPath.groups.map((group) => {
      const facts = [
        group.names.length ? `<span><strong>Names</strong> ${escapeHtml(group.names.join(', '))}</span>` : '',
        group.reads.length ? `<span><strong>Reads</strong> ${escapeHtml(group.reads.join(', '))}</span>` : '',
        group.calls.length ? `<span><strong>Calls</strong> ${escapeHtml(group.calls.join(', '))}</span>` : ''
      ].filter(Boolean).join('');
      const entries = group.entries.map((dependency) => `<li>${sourceLink(dependency.source_span, `Line ${dependency.source_span.start_line}`)}<span>${escapeHtml(dependencyFacts(dependency))}</span></li>`).join('');
      return `<details class="dependency-group"><summary><span>${escapeHtml(group.kind.replaceAll('_', ' '))}</span><span class="count">${plural(group.count, 'site')}</span></summary><div class="fact-strip">${facts}</div><ul class="evidence-list">${entries}</ul></details>`;
    }).join('');
    const additional = returnPath.additionalSourceSpans.length
      ? `<div class="additional-sources"><strong>Additional claim sources</strong>${renderSources(returnPath.additionalSourceSpans)}</div>`
      : '';
    return `<details class="return-sites"><summary><span>Dependency evidence</span><span class="count">${returnPath.siteCount} sites · ${plural(returnPath.groups.length, 'group')}</span></summary>${groups}${additional}</details>`;
  };

  const renderClaimFacts = (statement) => {
    const facts = [
      statement.inputs?.length ? ['Inputs', statement.inputs.join(', ')] : undefined,
      statement.definitions?.length ? ['Local values', statement.definitions.join(', ')] : undefined,
      statement.weak_definitions?.length ? ['May change through access', statement.weak_definitions.join(', ')] : undefined,
      statement.calls?.length ? ['Calls', statement.calls.map((call) => call.text).join(', ')] : undefined
    ].filter(Boolean);
    return facts.length
      ? `<dl class="fact-grid">${facts.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('')}</dl>`
      : '';
  };

  const renderClaims = (claims, returnPathOffset = 0) => claims.map((claim, claimIndex) => {
    const presentation = claimPresentation(claim);
    const returnPath = card.view === 'return' && claim.kind === 'return_dependency' ? returnPathPresentation(claim) : undefined;
    const label = claim.statement.type || claim.kind;
    const pathNumber = returnPathOffset + claimIndex + 1;
    const pathHeading = returnPath
      ? `<div class="path-heading"><span class="eyebrow">Return path ${pathNumber}</span><code>${escapeHtml(returnPath.returnExpression)}</code>${returnPath.returnSpan ? sourceLink(returnPath.returnSpan, `Return at line ${returnPath.returnSpan.start_line}`) : ''}${returnPath.pathConditions.length ? `<p><strong>When</strong> ${escapeHtml(returnPath.pathConditions.map((item) => item.text || item.source_text || '?').join(' and '))}</p>` : ''}</div>`
      : '';
    const sourceExpression = presentation.sourceText ? `<p class="source-expression"><span>Source syntax</span><code>${escapeHtml(presentation.sourceText)}</code></p>` : '';
    const scope = presentation.scope?.kind === 'callee' ? `<p class="scope-note"><strong>Callee scope</strong> <code>${escapeHtml(presentation.scope.function)}</code>${presentation.scope.names.length ? ` · ${escapeHtml(presentation.scope.names.join(', '))}` : ''}</p>` : '';
    const conditionSource = presentation.conditionSourceText ? `<p class="detail-copy"><strong>Condition syntax</strong> <code>${escapeHtml(presentation.conditionSourceText)}</code></p>` : '';
    const handlers = presentation.handlerSpans.length ? `<div class="source-row">${presentation.handlerSpans.map((span) => sourceLink(span, `Handler · line ${span.start_line}`)).join('')}</div>` : '';
    const condition = 'condition' in claim.statement ? `<details class="subdetail"><summary>Structured condition</summary><pre>${escapeHtml(JSON.stringify(presentation.condition, null, 2))}</pre></details>` : '';
    const dependencies = renderReturnDependencies(returnPath, presentation.dependencies);
    const localDependencies = presentation.localCallDependencies.length
      ? `<details class="subdetail"><summary>Inputs carried through local calls · ${presentation.localCallDependencies.length}</summary><ul class="evidence-list">${presentation.localCallDependencies.map((dependency) => { const inputs = dependency.caller_inputs.length ? ` · caller inputs: ${dependency.caller_inputs.join(', ')}` : ''; return `<li><span><code>${escapeHtml(`${dependency.callee_scope}.${dependency.callee_parameter}`)}</code> = <code>${escapeHtml(dependency.caller_argument)}</code><small>${escapeHtml(`${dependency.binding_origin}${inputs}`)}</small></span>${renderCallChain(dependency.call_chain)}</li>`; }).join('')}</ul></details>`
      : '';
    const observation = presentation.evidenceClass === 'observed' ? `<details class="subdetail"><summary>Observed support</summary><pre>${escapeHtml(JSON.stringify(claim.evidence.detail, null, 2))}</pre></details>` : '';
    const evidenceDetails = `<details class="provenance"><summary>Evidence, assumptions, and limits</summary><dl class="metadata"><div><dt>Method</dt><dd><code>${escapeHtml(presentation.method)}</code></dd></div>${presentation.boundaryIds.length ? `<div><dt>Limited by</dt><dd>${escapeHtml(presentation.boundaryIds.join(', '))}</dd></div>` : ''}</dl>${presentation.assumptions.length ? `<ul class="assumption-list">${presentation.assumptions.map((assumption) => `<li>${escapeHtml(assumption)}</li>`).join('')}</ul>` : ''}${renderCallChain(presentation.callChain)}</details>`;
    const sources = returnPath?.compact ? '' : renderSources(presentation.sourceSpans);
    return `<article class="claim claim--${safeClass(claim.kind)}">${pathHeading}<div class="claim-meta"><span>${escapeHtml(label.replaceAll('_', ' '))}</span><span class="evidence-badge evidence-badge--${safeClass(presentation.evidenceClass)}">${escapeHtml(presentation.evidenceClass)}</span></div><h3>${escapeHtml(presentation.summary)}</h3>${sourceExpression}${scope}${conditionSource}${handlers}${renderClaimFacts(claim.statement)}${condition}${dependencies}${localDependencies}${observation}${sources}${evidenceDetails}</article>`;
  }).join('');

  const renderBoundaryGroup = (group) => {
    const isCall = ['routine_call', 'module_local', 'external_or_unresolved_call'].includes(group.boundaryClass);
    const siteLabel = `${isCall ? 'call' : 'source'} ${group.count === 1 ? 'site' : 'sites'}`;
    const sites = group.occurrences.map((occurrence) => `<li>${sourceLink(occurrence.sourceSpan, `Line ${occurrence.sourceSpan.start_line}`)}${renderCallChain(occurrence.callChain)}</li>`).join('');
    const locations = group.count === 1 ? `<div class="source-row">${sourceLink(group.occurrences[0].sourceSpan, `Open ${siteLabel}`)}</div>` : `<details class="subdetail"><summary>${group.count} source locations</summary><ol class="evidence-list">${sites}</ol></details>`;
    return `<article class="boundary-card"><div class="claim-meta"><span>${escapeHtml(group.boundaryClass.replaceAll('_', ' '))}</span><span class="count">${group.count} ${siteLabel}</span></div><h3><code>${escapeHtml(group.target)}</code></h3><p>${escapeHtml(group.reason)}</p>${group.claimKinds.length ? `<p class="detail-copy"><strong>Limits</strong> ${escapeHtml(group.claimKinds.join(', '))}</p>` : ''}${group.limitingBoundaryIds.length ? `<p class="detail-copy"><strong>Limited by</strong> ${escapeHtml(group.limitingBoundaryIds.join(', '))}</p>` : ''}${locations}</article>`;
  };

  const renderBoundaries = (boundaries) => {
    const groups = boundaryGroups({ ...card, boundaries });
    const important = groups.filter((group) => group.category !== 'routine');
    const routine = groups.filter((group) => group.category === 'routine');
    const routineSites = routine.reduce((total, group) => total + group.count, 0);
    return important.map(renderBoundaryGroup).join('') + (routine.length ? `<details class="routine-boundaries"><summary>${routineSites} routine unresolved call sites · ${plural(routine.length, 'group')}</summary>${routine.map(renderBoundaryGroup).join('')}</details>` : '');
  };

  const renderDiagnostics = (diagnostics) => diagnosticGroups({ diagnostics }).map((group) => {
    const sites = group.occurrences.map((occurrence) => `<li>${occurrence.sourceSpan ? sourceLink(occurrence.sourceSpan, `Line ${occurrence.sourceSpan.start_line}`) : '<span>No source location</span>'}${renderCallChain(occurrence.callChain)}</li>`).join('');
    const count = `${plural(group.reportCount, 'report')} · ${plural(group.siteCount, 'source site')}`;
    const locations = group.reportCount > 1 ? `<details class="subdetail"><summary>${count}</summary><ol class="evidence-list">${sites}</ol></details>` : `<ol class="evidence-list">${sites}</ol>`;
    return `<article class="diagnostic-card"><div class="claim-meta"><span>${escapeHtml(group.kind.replaceAll('_', ' '))}</span><span>${escapeHtml(group.analyses.join(', '))}</span></div><h3>${escapeHtml(group.message)}</h3>${locations}</article>`;
  }).join('');

  const observationStatus = card.observation_status;
  const observationView = observationPresentation(observationStatus);
  const observationSummary = observationStatus
    ? `<article class="observation-card"><div class="claim-meta"><span>Test observation status</span><span>${escapeHtml(observationStatus.state.replaceAll('_', ' '))}</span></div><h3>${escapeHtml(observationView.message)}</h3><p>${escapeHtml(observationView.executionSummary)} · ${escapeHtml(observationView.distinctInputSummary)}</p>${observationView.excludedParameters.length ? `<p class="detail-copy"><strong>Excluded parameters</strong> ${escapeHtml(observationView.excludedParameters.map((parameter) => parameter.name).join(', '))}</p>` : ''}${observationStatus.tests.length ? `<p class="detail-copy"><strong>Tests</strong> ${escapeHtml(observationStatus.tests.join(', '))}</p>` : ''}</article>`
    : `<div class="quiet-state"><strong>No test observations yet.</strong><span>Run this function’s tests when static evidence is not enough.</span></div>`;

  const section = (id, eyebrow, title, count, content, empty = 'Nothing to show.') => `<section id="${id}" aria-labelledby="${id}-title"><div class="section-heading"><div><span class="eyebrow">${escapeHtml(eyebrow)}</span><h2 id="${id}-title">${escapeHtml(title)}</h2></div>${count === undefined ? '' : `<span class="section-count">${count}</span>`}</div>${content || `<p class="quiet-state">${escapeHtml(empty)}</p>`}</section>`;
  const derived = card.claims.filter((claim) => claim.evidence.evidence_class !== 'observed');
  const observed = card.claims.filter((claim) => claim.evidence.evidence_class === 'observed');
  const overviewClaimGroups = [
    ['Returns', 'Which values and branches can feed the result?', derived.filter((claim) => claim.kind === 'return_dependency')],
    ['Writes & effects', 'What state or external system can this function touch?', derived.filter((claim) => ['attempted_write', 'known_effect'].includes(claim.kind))],
    ['Failures', 'Which inputs are rejected, and which exceptions can escape?', derived.filter((claim) => ['rejected_input', 'explicit_exception'].includes(claim.kind))],
    ['Other facts', 'Supported evidence that does not fit a focused question.', derived.filter((claim) => !['return_dependency', 'attempted_write', 'known_effect', 'rejected_input', 'explicit_exception'].includes(claim.kind))]
  ].filter(([, , claims]) => claims.length);
  const renderOverviewClaimGroups = () => overviewClaimGroups.map(([label, question, claims]) => `<details class="overview-claim-group"><summary><span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(question)}</small></span><span class="count">${plural(claims.length, 'claim')}</span></summary>${renderClaims(claims)}</details>`).join('');
  const allBoundaryGroups = boundaryGroups(card);
  const diagnosticGroupCount = diagnosticGroups(card).length;
  const metrics = view.name === 'full'
    ? [['Derived', derived.length], ['Observed', observed.length], ['Boundaries', allBoundaryGroups.length], ['Diagnostics', diagnosticGroupCount]]
    : [['Claims in view', card.claims.length], ['Boundaries in view', allBoundaryGroups.length], ['Diagnostics in view', diagnosticGroupCount], ['Local calls', partition.groups.length]];
  const fullContent = [
    section('derived', 'Supported facts', 'What Saga derived', derived.length, renderOverviewClaimGroups(), 'No derived claims.'),
    section('observed', 'Execution evidence', 'What tests observed', observed.length, renderClaims(observed) + observationSummary),
    section('boundaries', 'Analysis limits', 'Where Saga stopped', allBoundaryGroups.length, renderBoundaries(card.boundaries), 'No recorded boundaries.'),
    section('diagnostics', 'Analyzer reports', 'What needs attention', diagnosticGroupCount, renderDiagnostics(card.diagnostics), 'No diagnostics.')
  ].join('');

  const directClaims = partition?.direct.claims || card.claims;
  const directBoundaries = partition?.direct.boundaries || card.boundaries;
  const directDiagnostics = partition?.direct.diagnostics || card.diagnostics;
  const directReturnCount = directClaims.filter((claim) => claim.kind === 'return_dependency').length;
  let localReturnOffset = directReturnCount;
  const localCallGroups = (partition?.groups || []).map((group) => {
    const groupedBoundaries = boundaryGroups({ ...card, boundaries: group.boundaries });
    const groupedDiagnostics = diagnosticGroups({ diagnostics: group.diagnostics });
    const counts = [plural(group.claims.length, 'claim'), plural(groupedBoundaries.length, 'boundary group'), plural(groupedDiagnostics.length, 'diagnostic group')].join(' · ');
    const claims = group.claims.length ? `<h3 class="inside-heading">Claims</h3>${renderClaims(group.claims, localReturnOffset)}` : '';
    localReturnOffset += group.claims.filter((claim) => claim.kind === 'return_dependency').length;
    const boundaries = group.boundaries.length ? `<h3 class="inside-heading">Boundaries</h3>${renderBoundaries(group.boundaries)}` : '';
    const diagnostics = group.diagnostics.length ? `<h3 class="inside-heading">Diagnostics</h3>${renderDiagnostics(group.diagnostics)}` : '';
    const bindings = group.argumentBindings.length ? `<p class="detail-copy"><strong>Arguments</strong> ${escapeHtml(group.argumentBindings.map((item) => `${item.parameter} = ${item.argument}`).join(', '))}</p>` : '';
    return `<details class="local-call-evidence"><summary><span><code>${escapeHtml(group.invokedAs)}(...)</code><small>${escapeHtml(counts)}</small></span><span class="disclosure-label">Inspect call</span></summary><div class="link-row">${sourceLink(group.callSite, `Call · line ${group.callSite.start_line}`)} ${sourceLink(group.calleeSpan, `Callee · line ${group.calleeSpan.start_line}`)}</div>${bindings}${claims}${boundaries}${diagnostics}</details>`;
  }).join('');

  const empty = view.empty ? `<div class="empty-state" role="note"><strong>No supported evidence in this view.</strong><p>${escapeHtml(view.emptyMessage)}</p></div>` : '';
  const focusedClaims = view.name === 'boundary' ? '' : section('answer', 'Direct evidence', view.label, directClaims.length, empty || renderClaims(directClaims));
  const directBoundaryCount = boundaryGroups({ ...card, boundaries: directBoundaries }).length;
  const focusedBoundaries = section('related-boundaries', view.name === 'boundary' ? 'Direct evidence' : 'Limits on this answer', view.name === 'boundary' ? 'Analysis boundaries' : 'Related boundaries', directBoundaryCount, view.name === 'boundary' && empty ? empty : renderBoundaries(directBoundaries), 'No recorded boundary limits this direct evidence.');
  const focusedDiagnostics = directDiagnostics.length ? section('target-diagnostics', 'Analyzer reports', 'Related diagnostics', diagnosticGroups({ diagnostics: directDiagnostics }).length, renderDiagnostics(directDiagnostics)) : '';
  const propagated = localCallGroups ? section('local-calls', 'One-hop analysis', 'Evidence inside local calls', partition.groups.length, localCallGroups) : '';
  const hiddenDiagnostics = view.hiddenDiagnosticMessage ? `<p class="diagnostic-pointer">${escapeHtml(view.hiddenDiagnosticMessage)}</p>` : '';
  const focusedObservations = observationStatus ? section('test-status', 'Execution evidence', 'Test observation status', undefined, observationSummary) : '';
  const focusedContent = focusedClaims + focusedBoundaries + focusedObservations + focusedDiagnostics + propagated + hiddenDiagnostics;

  const navItems = [['full', 'Overview'], ['return', 'Returns'], ['mutation', 'Writes & effects'], ['failure', 'Failures'], ['boundary', 'Limits']];
  const navigation = navItems.map(([name, label]) => commandLink(
    'saga.openEvidenceCard',
    request(name),
    label,
    name === view.name ? 'tab is-active' : 'tab',
    name === view.name ? 'aria-current="page"' : ''
  )).join('');
  const targetLocation = card.target.source_span ? sourceLink(card.target.source_span, `${card.target.path}:${card.target.source_span.start_line}`) : `<span>${escapeHtml(card.target.path)}</span>`;
  const runTests = commandLink('saga.runTests', request(view.name), 'Run tests for this function', 'primary-action');

  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${panel.webview.cspSource};"><title>${escapeHtml(card.target.name)} · Saga evidence</title><link rel="stylesheet" href="${escapeHtml(styleUri)}"></head><body><header class="hero"><div class="hero-inner"><span class="eyebrow">Function evidence</span><div class="title-row"><h1>${escapeHtml(card.target.name)}</h1><span class="status status--${safeClass(card.target.status)}">${escapeHtml(card.target.status)}</span></div><code class="signature">${escapeHtml(card.target.signature)}</code><div class="target-location">${targetLocation}</div><dl class="metrics">${metrics.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${value}</dd></div>`).join('')}</dl></div></header><nav class="view-tabs" aria-label="Evidence views">${navigation}</nav><main><div class="question-bar"><p>${escapeHtml(VIEW_COPY[view.name])}</p>${runTests}</div>${view.name === 'full' ? fullContent : focusedContent}</main><footer><strong>Evidence classes stay separate.</strong> Derived facts come from analysis. Observed facts come only from recorded test runs.</footer></body></html>`;
}

module.exports = { panelHtml };
