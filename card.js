const fs = require('node:fs');
const path = require('node:path');

const VIEW_CLAIMS = {
  return: new Set(['return_dependency']),
  mutation: new Set(['attempted_write', 'known_effect']),
  failure: new Set(['rejected_input', 'explicit_exception']),
  boundary: new Set()
};

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
    condition: claim.statement.condition,
    handlerSpans: claim.statement.handler_spans || [],
    callChain: claim.call_chain || [],
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
  return { ...card, view, claims, boundaries, diagnostics: [...card.diagnostics] };
}

function viewPresentation(card) {
  /** Describe the active view and its honest empty state. */
  const view = card.view || 'full';
  const empty = ['boundary', 'mutation'].includes(view) ? card.claims.length === 0 && card.boundaries.length === 0 : card.claims.length === 0;
  return {
    name: view,
    label: VIEW_LABELS[view],
    empty,
    emptyMessage: empty ? EMPTY_MESSAGES[view] : undefined
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
    group.claimKinds = [...new Set([
      ...group.claimKinds,
      ...(claimsByBoundary.get(boundary.id) || [])
    ])].sort();
    group.occurrences.push({
      boundaryId: boundary.id,
      sourceSpan: boundary.source_span,
      callChain: boundary.call_chain || []
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
      group = { kind: diagnostic.kind, message: diagnostic.message, reportCount: 0, occurrences: [], occurrenceKeys: new Set() };
      byKey.set(key, group);
      groups.push(group);
    }
    group.reportCount += 1;
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

module.exports = { loadCard, targetNameFromLine, validateCard, claimPresentation, focusCard, viewPresentation, boundaryGroups, diagnosticGroups, hoverLines };
