# Resume evidence — CodeCairn

- Built an auditable long-term memory runtime for coding agents with Markdown truth, SQLite state, LanceDB hybrid retrieval, evidence gates, resumable import, and 148 automated tests at 83.50% coverage.
- Evaluated 96% Recall@5, 0.798 MRR, and 10.91 ms P95 latency over 100 isolated queries; reproduced the index from Markdown truth with 100% consistency.
- Ran 120 isolated hidden-verifier CodingMemoryBench trials; memory-on raised pass rate from 85% to 100% (+15 pp), reduced total tokens by 2.26%, and shortened steps to first useful action by 3.41%.
- Ingested all 10 official LoCoMo conversations (272 sessions, 5882 turns) into 5882 evidence-backed memories with 0 gate rejections; completed an explicitly unscored 10-question end-to-end smoke run with zero infrastructure failures.

## Pending — do not publish as measured

- LoCoMo accuracy: pending.
- CodingMemoryBench provider cost: pending.
- LoCoMo provider cost: pending.
