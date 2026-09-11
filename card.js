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
  };
}

function hoverLines(card) {
  /** Render the compact hover surface while leaving full detail to the panel. */
  const lines = ['**Saga** · ' + card.target.signature];
  const groups = [['rejected_input', 'Guard'], ['test_observation', 'Observed'], ['boundary', 'Boundary'], ['return_dependency', 'Return'], ['attempted_write', 'Write'], ['known_effect', 'Effect']];
  for (const [kind, label] of groups) {
    if (lines.length >= 5) break;
    const claim = card.claims.find((item) => item.kind === kind);
    const boundary = card.boundaries.find((item) => kind === 'boundary' && item.category !== 'routine') || card.boundaries.find((item) => kind === 'boundary');
    const item = claim || boundary;
    if (item) {
      const source = item.source_spans ? item.source_spans[0] : item.source_span;
      const span = encodeURIComponent(JSON.stringify(source));
      const text = claim ? claim.statement.text : item.target.text + ' is unresolved';
      lines.push('- [' + label + '](command:saga.navigate?' + span + '): ' + text);
    }
  }
  const target = encodeURIComponent(JSON.stringify({ path: card.target.path, name: card.target.name }));
  lines.push('[Open detailed evidence card](command:saga.openEvidenceCard?' + target + ')');
  return lines;
}

module.exports = { loadCard, targetNameFromLine, validateCard, claimPresentation, hoverLines };
