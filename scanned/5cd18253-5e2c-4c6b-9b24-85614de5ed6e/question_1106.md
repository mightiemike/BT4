# Q1106: energy-undercharge in InternalTransaction.getParentHash

## Question
Can an unprivileged attacker use /wallet/broadcasttransaction to make chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java::getParentHash perform substantially more work than the Energy charged, or refund Energy that should remain burned, leading to Materially underpriced public execution work or deterministic node degradation on smart-contract input?

## Target
- File/function: chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java::getParentHash
- Entrypoint: /wallet/broadcasttransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Target expansion, nested calls, precompiles, native opcodes, and exceptional exits where charge and refund accounting may diverge.
- Invariant to test: Charged Energy must conservatively upper-bound the real execution work and refunds must never exceed what was validly earned.
- Expected Immunefi impact: Materially underpriced public execution work or deterministic node degradation on smart-contract input
- Fast validation: Fuzz contracts that maximize work per charged unit via /wallet/broadcasttransaction; compare measured execution effort against charged and refunded Energy.
