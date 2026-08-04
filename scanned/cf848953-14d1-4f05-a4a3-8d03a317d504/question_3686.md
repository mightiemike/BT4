# Q3686: filter-range cost gap in TransactionResult.parseSignature

## Question
Can an unprivileged attacker abuse gRPC broadcastTransaction with large ranges, topic sets, or repeated filter polling so framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionResult.java::parseSignature performs materially underpriced block, log, or state iteration and degrades a production node below the real cost of the request?

## Target
- File/function: framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionResult.java::parseSignature
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Push filter-count, range-width, topic-cardinality, and repeated polling edges while staying syntactically valid.
- Invariant to test: Public query work must be bounded and charged or limited in proportion to the real block/log/state iteration it triggers.
- Expected Immunefi impact: Materially underpriced public deserialization or broadcast work
- Fast validation: Benchmark worst-case filters and ranges via gRPC broadcastTransaction; flag inputs where attacker-controlled work scales far faster than request cost or limits.
