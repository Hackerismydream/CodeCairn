# Recall Uses Vector and Lexical Candidate Union

Recall builds the union of vector and real lexical candidates before reranking.
Lexical retrieval must not be limited to a vector shortlist. Ranking records the
candidate sources, component scores, final order, and latency in the JSON
sidecar.

Recall Context is Markdown first and task-shaped; it is not a raw search-result
dump.
