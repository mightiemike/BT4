# Q3679: retry-broadcast race in TransactionReceipt.class-level path

## Question
Can an unprivileged attacker use public retries around /wallet/broadcasttransaction so framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionReceipt.java::class-level path accepts the same logical request from multiple surfaces or timing windows, causing Replayed or double-applied transaction execution?

## Target
- File/function: framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionReceipt.java::class-level path
- Entrypoint: /wallet/broadcasttransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Probe raw/built transaction retries, mixed hex and JSON forms, and closely spaced repeats across HTTP, gRPC, and JSON-RPC.
- Invariant to test: Public retries must preserve one-time semantics and converge on one settlement outcome regardless of surface or serialization.
- Expected Immunefi impact: Replayed or double-applied transaction execution
- Fast validation: Race the same payload via all public broadcast surfaces and assert pending, recent, and final state converge to one settlement.
