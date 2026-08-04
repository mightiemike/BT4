# Q1108: precompile-canonicalization mismatch in InternalTransaction.getParentHash

## Question
Can an unprivileged attacker pass edge-case inputs through gRPC broadcastTransaction so chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java::getParentHash feeds a precompile or native contract with non-canonical data, causing different outputs, authorization results, or charges than another honest node would compute and leading to Deterministic invalid state divergence on public smart-contract input?

## Target
- File/function: chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java::getParentHash
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Probe length prefixes, empty values, non-canonical encodings, duplicate fields, and boundary inputs around native or precompiled operations.
- Invariant to test: For the same public input, every honest node must derive the same precompile inputs, outputs, charges, and side effects.
- Expected Immunefi impact: Deterministic invalid state divergence on public smart-contract input
- Fast validation: Differential-test the same contract call via gRPC broadcastTransaction across repeated executions and alternate encodings; assert identical result bytes, charges, and receipts.
