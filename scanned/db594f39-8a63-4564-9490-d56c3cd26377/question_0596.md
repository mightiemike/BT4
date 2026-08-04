# Q596: storage-key collision in OperationRegistry.getTable

## Question
Can an unprivileged attacker choose calldata or storage keys through /jsonrpc eth_call / eth_estimateGas / eth_sendRawTransaction so actuator/src/main/java/org/tron/core/vm/OperationRegistry.java::getTable normalizes two distinct logical keys into one internal slot, overwriting another user or contract state and leading to Unauthorized contract-state mutation or deterministic state divergence?

## Target
- File/function: actuator/src/main/java/org/tron/core/vm/OperationRegistry.java::getTable
- Entrypoint: /jsonrpc eth_call / eth_estimateGas / eth_sendRawTransaction
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Probe zero-padding, sign extension, truncation, prefix handling, and alternate encodings of the same apparent storage key.
- Invariant to test: Each logical storage location must map to one unique internal slot and never collide with attacker-controlled alternatives.
- Expected Immunefi impact: Unauthorized contract-state mutation or deterministic state divergence
- Fast validation: Generate equivalent-looking but byte-distinct keys via /jsonrpc eth_call / eth_estimateGas / eth_sendRawTransaction, execute writes, and assert no unrelated slot changes.
