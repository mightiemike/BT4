# Q1112: storage-key collision in InternalTransaction.getTransaction

## Question
Can an unprivileged attacker choose calldata or storage keys through /jsonrpc eth_sendRawTransaction so chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java::getTransaction normalizes two distinct logical keys into one internal slot, overwriting another user or contract state and leading to Unauthorized contract-state mutation or deterministic state divergence?

## Target
- File/function: chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java::getTransaction
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Probe zero-padding, sign extension, truncation, prefix handling, and alternate encodings of the same apparent storage key.
- Invariant to test: Each logical storage location must map to one unique internal slot and never collide with attacker-controlled alternatives.
- Expected Immunefi impact: Unauthorized contract-state mutation or deterministic state divergence
- Fast validation: Generate equivalent-looking but byte-distinct keys via /jsonrpc eth_sendRawTransaction, execute writes, and assert no unrelated slot changes.
