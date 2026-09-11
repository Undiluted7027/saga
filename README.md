# Saga: evidence-backed Python code intelligence

## Slice 0 VS Code prototype

The repository currently contains the fixture-backed evidence-card prototype from
POC ticket #1. It uses the serialized payload at
`fixture/process_order.card.json`, validated against
`schema/evidence-card.schema.json`.

To run its focused tests:

```sh
npm test
```

To try the editor flow, open this repository in VS Code, install the repository
as an extension through the Extension Development Host (`Run Extension`), and
open `fixture/process_order.py`. Hover over `process_order`, then open the
detailed card and select a source link. The prototype intentionally uses fixture
data; real analysis, caching, and indexing belong to later slices.
