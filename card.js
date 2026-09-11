const fs = require('node:fs');
const path = require('node:path');

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
    condition: claim.statement.condition,
    handlerSpans: claim.statement.handler_spans || [],
    callChain: claim.call_chain || [],
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
  const target = encodeURIComponent(JSON.stringify({ path: card.target.path, name: card.target.name }));
  lines.push('[Open detailed evidence card](command:saga.openEvidenceCard?' + target + ')');
  return lines;
}

module.exports = { loadCard, targetNameFromLine, validateCard, claimPresentation, boundaryGroups, hoverLines };
