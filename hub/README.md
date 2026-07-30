# CodeCairn Memory Hub prototype

A converged, mock-data product prototype for the human control plane of
CodeCairn, an evidence-first Agent Memory OS.

The chosen direction combines:

- a causal journey that explains how a task becomes memory and affects a later task;
- an always-visible memory inspector for evidence, evolution, and recall decisions;
- persistent system health that makes the runtime feel like an OS rather than a query tool.

The prototype includes Today, Memories, Recalls, and Activity views. Query
parameters can open a review state directly:

- `?view=today&detail=evidence`
- `?view=memories&detail=evolution`
- `?view=recalls&detail=recall`
- `?view=activity&detail=content`

It is read-only and does not connect to a real CodeCairn installation.

## Local development

```bash
npm install
npm run dev
npm test
```

Node.js `>=22.13.0` is required.
