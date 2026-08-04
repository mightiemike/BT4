# Q3678: api-surface inconsistency in TransactionReceipt.class-level path

## Question
Can an unprivileged attacker invoke the same logical action through /jsonrpc eth_sendRawTransaction and an alternate HTTP/gRPC/JSON-RPC path so framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionReceipt.java::class-level path applies different normalization or guard logic, with the weaker path leading to Unauthorized or duplicate settlement via transaction-processing confusion?

## Target
- File/function: framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionReceipt.java::class-level path
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Cross-check every public surface that can build, simulate, or broadcast the same transaction or query.
- Invariant to test: Equivalent public surfaces must normalize the same fields, enforce the same guards, and return the same decision for one logical action.
- Expected Immunefi impact: Unauthorized or duplicate settlement via transaction-processing confusion
- Fast validation: Replay identical inputs through all public surfaces and diff selected owner, contract, tx bytes, and rejection reasons.
