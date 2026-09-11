const fs = require('node:fs');
const path = require('node:path');

function loadCard(filePath = path.join(__dirname, 'fixture', 'process_order.card.json')) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function validateCard(card) {
  const required = ['schema_version', 'target', 'claims', 'boundaries', 'diagnostics'];
  const errors = required.filter((key) => !(key in card)).map((key) => `missing ${key}`);
  if (card.schema_version !== '0.1') errors.push('schema_version must be 0.1');
  if (!Array.isArray(card.claims)) errors.push('claims must be an array');
  if (!Array.isArray(card.boundaries)) errors.push('boundaries must be an array');
  for (const claim of card.claims || []) {
    if (!claim.id || !claim.kind || !claim.statement?.text || !claim.evidence || !claim.source_spans?.length) {
      errors.push(`claim ${claim.id || '<unknown>'} is incomplete`);
    }
  }
  return errors;
}

function hoverLines(card) {
  const lines = ['**Saga** · ' + card.target.signature];
  const groups = [['rejected_input', 'Guard'], ['return_dependency', 'Return'], ['known_effect', 'Effect'], ['test_observation', 'Observed']];
  for (const [kind, label] of groups) {
    const claim = card.claims.find((item) => item.kind === kind);
    if (claim) {
      const span = encodeURIComponent(JSON.stringify(claim.source_spans[0]));
      lines.push('- [' + label + '](command:saga.navigate?' + span + '): ' + claim.statement.text);
    }
  }
  lines.push('[Open detailed evidence card](command:saga.openEvidenceCard)');
  return lines;
}

module.exports = { loadCard, validateCard, hoverLines };
