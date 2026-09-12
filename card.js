const fs = require('node:fs');
const path = require('node:path');

const VIEW_CLAIMS = {
  return: new Set(['return_dependency']),
  mutation: new Set(['attempted_write', 'known_effect']),
  failure: new Set(['rejected_input', 'explicit_exception']),
  boundary: new Set()
};

const VIEW_DIAGNOSTIC_ANALYSES = {
  return: new Set(['inspection', 'returns']),
  mutation: new Set(['inspection', 'effects']),
  failure: new Set(['inspection', 'guards', 'exceptions']),
  boundary: new Set(['inspection'])
};

function hiddenDiagnosticSummary(groupCount) {
  const noun = groupCount === 1 ? 'group' : 'groups';
  return {
    group_count: groupCount,
    message: `${groupCount} diagnostic ${noun} hidden; open the full card to inspect them.`
  };
}

const VIEW_LABELS = {
  full: 'Full evidence card',
  return: 'Return dependencies',
  mutation: 'Mutations and effects',
  failure: 'Failures',
  boundary: 'Analysis boundaries'
};

const EMPTY_MESSAGES = {
  full: 'Saga produced no claims or boundaries for this function.',
  return: 'Saga found no supported return-dependency evidence. This does not establish that the function cannot return or that its return is independent of other values.',
  mutation: 'Saga found no supported mutation or known-effect evidence. This does not establish that the function is pure or cannot change state.',
  failure: 'Saga found no supported rejected-input or explicit-exception evidence. This does not establish that the function cannot fail or raise an exception.',
  boundary: 'Saga recorded no analysis boundaries for this function. This does not establish complete analysis. Check the full card for diagnostics.'
};

function loadCard(filePath = path.join(__dirname, 'fixture', 'process_order.card.json')) {
  /** Load one serialized card; Slice 0 keeps this deterministic fixture path. */
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function targetNameFromLine(line) {
  /** Extract only a module-level function name, including when the cursor is on `def`. */
  const match = line.match(/^(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(/);
  return match ? match[1] : undefined;
}

function validateCard(card) {
  /** Return contract errors without changing or interpreting the card. */
  const required = ['schema_version', 'target', 'claims', 'boundaries', 'diagnostics'];
  const errors = required.filter((key) => !(key in card)).map((key) => `missing ${key}`);
  if (card.schema_version !== '0.1') errors.push('schema_version must be 0.1');
  if (!card.target?.status || !['supported', 'unsupported'].includes(card.target.status)) errors.push('target.status must be supported or unsupported');
  if (!Array.isArray(card.claims)) errors.push('claims must be an array');
  if (!Array.isArray(card.boundaries)) errors.push('boundaries must be an array');
  for (const claim of card.claims || []) {
    if (!claim.id || !claim.kind || !claim.statement?.text || !claim.evidence || !claim.source_spans?.length) {
      errors.push(`claim ${claim.id || '<unknown>'} is incomplete`);
    }
  }
  return errors;
}

function claimPresentation(claim) {
  /** Expose card wording and its evidence without inventing editor-only meaning. */
  return {
    summary: claim.statement.text,
    sourceText: claim.statement.source_text,
    conditionSourceText: claim.statement.condition_source_text,
    evidenceClass: claim.evidence.evidence_class,
    method: claim.evidence.method,
    assumptions: claim.assumptions.map((item) => item.text),
    boundaryIds: [...claim.boundary_ids],
    sourceSpans: [...claim.source_spans],
    dependencies: claim.statement.dependencies || [],
    localCallDependencies: claim.statement.local_call_dependencies || [],
    scope: claim.statement.scope,
    condition: claim.statement.condition,
    handlerSpans: claim.statement.handler_spans || [],
    callChain: claim.call_chain || [],
  };
}

const RETURN_SITE_LIMIT = 8;
const RETURN_DEPENDENCY_KINDS = ['return', 'definition', 'weak_definition', 'control_predicate', 'statement'];

function returnPathPresentation(claim) {
  /** Group existing dependency entries for display without changing the claim. */
  const dependencies = claim.statement?.dependencies || [];
  const byKind = new Map(RETURN_DEPENDENCY_KINDS.map((kind) => [kind, []]));
  const extraKinds = [];
  for (const dependency of dependencies) {
    const kind = dependency.kind || 'statement';
    if (!byKind.has(kind)) {
      byKind.set(kind, []);
      extraKinds.push(kind);
    }
    byKind.get(kind).push(dependency);
  }
  const groups = [...RETURN_DEPENDENCY_KINDS, ...extraKinds].flatMap((kind) => {
    const entries = byKind.get(kind);
    if (!entries.length) return [];
    const union = (field) => [...new Set(entries.flatMap((item) => item[field] || []))].sort();
    return [{
      kind,
      count: entries.length,
      names: union('names'),
      reads: union('reads'),
      calls: [...new Set(entries.flatMap((item) => (item.calls || []).map((call) => call.text)))].sort(),
      entries
    }];
  });
  const returnEntry = dependencies.find((item) => item.kind === 'return');
  const spanKey = (span) => JSON.stringify([
    span.path,
    span.start_line,
    span.start_column,
    span.end_line,
    span.end_column
  ]);
  const dependencySpanKeys = new Set(dependencies.map((item) => spanKey(item.source_span)));
  return {
    compact: dependencies.length > RETURN_SITE_LIMIT,
    siteCount: dependencies.length,
    returnExpression: claim.statement.return_expression || claim.statement.source_text || '?',
    pathConditions: claim.statement.path_conditions || [],
    returnSpan: claim.evidence?.detail?.return_source_span || returnEntry?.source_span || claim.source_spans?.at(-1),
    groups,
    dependencies,
    sourceSpans: claim.source_spans || [],
    additionalSourceSpans: (claim.source_spans || []).filter((span) => !dependencySpanKeys.has(spanKey(span)))
  };
}

function focusCard(card, view = 'full') {
  /** Return a fixed projection while retaining the original evidence objects. */
  if (view === 'full') return card;
  const allowed = VIEW_CLAIMS[view];
  if (!allowed) throw new Error(`Unknown Saga view: ${view}`);
  const claims = card.claims.filter((claim) => allowed.has(claim.kind));
  const relatedIds = new Set(claims.flatMap((claim) => claim.boundary_ids));
  const boundaries = view === 'boundary'
    ? [...card.boundaries]
    : card.boundaries.filter((boundary) => relatedIds.has(boundary.id) || (view === 'mutation' && boundary.concerns?.includes('effects')));
  const relevantAnalyses = VIEW_DIAGNOSTIC_ANALYSES[view];
  const diagnostics = card.diagnostics.filter((diagnostic) =>
    (diagnostic.analyses || ['inspection']).some((analysis) => relevantAnalyses.has(analysis))
  );
  const diagnosticSet = new Set(diagnostics);
  const hidden = card.diagnostics.filter((diagnostic) => !diagnosticSet.has(diagnostic));
  const focused = { ...card, view, claims, boundaries, diagnostics };
  if (hidden.length) {
    focused.hidden_diagnostics = hiddenDiagnosticSummary(
      diagnosticGroups({ diagnostics: hidden }).length
    );
  } else {
    delete focused.hidden_diagnostics;
  }
  return focused;
}

function localCallEvidence(card) {
  /** Separate direct evidence from evidence grouped beneath its first local call. */
  const direct = { claims: [], boundaries: [], diagnostics: [] };
  const groups = [];
  const byKey = new Map();
  const spanKey = (span) => [
    span.path,
    span.start_line,
    span.start_column,
    span.end_line,
    span.end_column
  ];
  for (const collection of ['claims', 'boundaries', 'diagnostics']) {
    for (const item of card[collection] || []) {
      const chain = item.call_chain || [];
      if (!chain.length) {
        direct[collection].push(item);
        continue;
      }
      const link = chain[0];
      const invokedAs = link.invoked_as || link.callee;
      const key = JSON.stringify([
        link.caller,
        link.callee,
        invokedAs,
        ...spanKey(link.call_site)
      ]);
      let group = byKey.get(key);
      if (!group) {
        group = {
          caller: link.caller,
          callee: link.callee,
          invokedAs,
          callSite: link.call_site,
          calleeSpan: link.callee_span,
          argumentBindings: link.argument_bindings || [],
          claims: [],
          boundaries: [],
          diagnostics: []
        };
        byKey.set(key, group);
        groups.push(group);
      }
      group[collection].push(item);
    }
  }
  groups.sort((left, right) => {
    const leftKey = [...spanKey(left.callSite), left.caller, left.callee];
    const rightKey = [...spanKey(right.callSite), right.caller, right.callee];
    return JSON.stringify(leftKey).localeCompare(JSON.stringify(rightKey), undefined, { numeric: true });
  });
  return { direct, groups };
}

function viewPresentation(card) {
  /** Describe the active view and its honest empty state. */
  const view = card.view || 'full';
  const empty = ['boundary', 'mutation'].includes(view) ? card.claims.length === 0 && card.boundaries.length === 0 : card.claims.length === 0;
  return {
    name: view,
    label: VIEW_LABELS[view],
    empty,
    emptyMessage: empty ? EMPTY_MESSAGES[view] : undefined,
    hiddenDiagnosticMessage: card.hidden_diagnostics?.message
  };
}

function observationPresentation(status) {
  /** Expose runtime status fields without interpreting observations as proof. */
  if (!status) return undefined;
  return {
    message: status.message,
    executionSummary: `${status.execution_count} executions · ${status.returned_executions} returned · ${status.raised_executions} raised`,
    distinctInputSummary: `${status.distinct_inputs} distinct usable inputs`,
    excludedParameters: status.excluded_parameters || [],
    inputDomain: status.input_domain || {}
  };
}

function boundaryClass(boundary) {
  /** Classify why analysis stopped without changing the raw boundary. */
  if (boundary.category === 'routine') return 'routine_call';
  if (['local_call_limit', 'unsupported_local_callee', 'ambiguous_local_callee'].includes(boundary.kind)) return 'module_local';
  if (['dynamic_dispatch', 'assignment_hooks'].includes(boundary.kind)) return 'dynamic_behavior';
  if (['exception_dispatch', 'exception_matching'].includes(boundary.kind)) return 'exception_flow';
  if (boundary.kind === 'unresolved_call') return 'external_or_unresolved_call';
  return 'unsupported_behavior';
}

function boundaryGroups(card) {
  /** Group equal stop reasons while retaining every boundary id and source span. */
  const claimsByBoundary = new Map();
  for (const claim of card.claims || []) {
    for (const boundaryId of claim.boundary_ids || []) {
      if (!claimsByBoundary.has(boundaryId)) claimsByBoundary.set(boundaryId, new Set());
      claimsByBoundary.get(boundaryId).add(claim.kind);
    }
  }
  const groups = [];
  const byKey = new Map();
  for (const boundary of card.boundaries || []) {
    const key = JSON.stringify([boundary.kind, boundary.target.text, boundary.reason]);
    let group = byKey.get(key);
    if (!group) {
      group = {
        kind: boundary.kind,
        target: boundary.target.text,
        reason: boundary.reason,
        category: boundary.category || 'important',
        boundaryClass: boundaryClass(boundary),
        boundaryIds: [],
        limitingBoundaryIds: [],
        claimKinds: [],
        occurrences: []
      };
      byKey.set(key, group);
      groups.push(group);
    }
    if (boundary.category !== 'routine') {
      group.category = 'important';
      group.boundaryClass = boundaryClass({ ...boundary, category: 'important' });
    }
    group.boundaryIds.push(boundary.id);
    group.limitingBoundaryIds = [...new Set([
      ...group.limitingBoundaryIds,
      ...(boundary.boundary_ids || [])
    ])].sort();
    group.claimKinds = [...new Set([
      ...group.claimKinds,
      ...(claimsByBoundary.get(boundary.id) || [])
    ])].sort();
    group.occurrences.push({
      boundaryId: boundary.id,
      sourceSpan: boundary.source_span,
      callChain: boundary.call_chain || [],
      boundaryIds: boundary.boundary_ids || []
    });
  }
  for (const group of groups) group.count = group.occurrences.length;
  return groups;
}

function diagnosticGroups(card) {
  /** Group equal diagnostics for display while retaining distinct source sites. */
  const groups = [];
  const byKey = new Map();
  for (const diagnostic of card.diagnostics || []) {
    const key = JSON.stringify([diagnostic.kind, diagnostic.message]);
    let group = byKey.get(key);
    if (!group) {
      group = { kind: diagnostic.kind, message: diagnostic.message, analyses: [], reportCount: 0, occurrences: [], occurrenceKeys: new Set() };
      byKey.set(key, group);
      groups.push(group);
    }
    group.reportCount += 1;
    group.analyses = [...new Set([
      ...group.analyses,
      ...(diagnostic.analyses || ['inspection'])
    ])].sort();
    const occurrenceKey = JSON.stringify([
      diagnostic.source_span || null,
      (diagnostic.call_chain || []).map((link) => link.call_site)
    ]);
    if (!group.occurrenceKeys.has(occurrenceKey)) {
      group.occurrenceKeys.add(occurrenceKey);
      group.occurrences.push({ sourceSpan: diagnostic.source_span, callChain: diagnostic.call_chain || [] });
    }
  }
  for (const group of groups) {
    group.siteCount = group.occurrences.length;
    delete group.occurrenceKeys;
  }
  return groups;
}

function hoverLines(card) {
  /** Render the compact hover surface while leaving full detail to the panel. */
  const lines = ['**Saga** · ' + card.target.signature];
  const groups = [['rejected_input', 'Guard'], ['test_observation', 'Observed'], ['boundary', 'Boundary'], ['return_dependency', 'Return'], ['attempted_write', 'Write'], ['known_effect', 'Effect']];
  const groupedBoundaries = boundaryGroups(card);
  for (const [kind, label] of groups) {
    if (lines.length >= 5) break;
    const claim = card.claims.find((item) => item.kind === kind);
    const boundary = groupedBoundaries.find((item) => kind === 'boundary' && item.category !== 'routine') || groupedBoundaries.find((item) => kind === 'boundary');
    const item = claim || boundary;
    if (item) {
      const source = item.source_spans ? item.source_spans[0] : item.occurrences[0].sourceSpan;
      const span = encodeURIComponent(JSON.stringify(source));
      const sites = item.count > 1 ? ` at ${item.count} sites` : '';
      const text = claim ? claim.statement.text : item.target + ' is unresolved' + sites;
      lines.push('- [' + label + '](command:saga.navigate?' + span + '): ' + text);
    }
  }
  const request = (view) => encodeURIComponent(JSON.stringify({ path: card.target.path, name: card.target.name, view }));
  lines.push(
    `[Full](command:saga.openEvidenceCard?${request('full')}) · ` +
    `[Return](command:saga.openEvidenceCard?${request('return')}) · ` +
    `[Mutation](command:saga.openEvidenceCard?${request('mutation')}) · ` +
    `[Failure](command:saga.openEvidenceCard?${request('failure')}) · ` +
    `[Boundaries](command:saga.openEvidenceCard?${request('boundary')})`
  );
  return lines;
}

module.exports = { loadCard, targetNameFromLine, validateCard, claimPresentation, returnPathPresentation, focusCard, localCallEvidence, viewPresentation, observationPresentation, boundaryGroups, diagnosticGroups, hoverLines };
