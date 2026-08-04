# Q1286: signer-threshold confusion in TransactionFactory.register

## Question
Can an unprivileged attacker use /jsonrpc eth_sendRawTransaction to craft duplicate, reordered, or aliased authorization inputs that make chainbase/src/main/java/org/tron/core/actuator/TransactionFactory.java::register count signer weight incorrectly, letting one broadcast, pending, receipt, or transaction-tracking flow pass without the true threshold and causing Unauthorized or duplicate settlement via transaction-processing confusion?

## Target
- File/function: chainbase/src/main/java/org/tron/core/actuator/TransactionFactory.java::register
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Stress duplicate signer references, permission_id selection, operations masks, and address alias forms to see whether sign weight is over-counted or the wrong permission branch is used.
- Invariant to test: Signer weight, operations masks, and permission selection must resolve once and only for the intended account/action.
- Expected Immunefi impact: Unauthorized or duplicate settlement via transaction-processing confusion
- Fast validation: Build multi-sign or restricted-permission cases, replay with reordered signers or aliased addresses via /jsonrpc eth_sendRawTransaction, and assert unauthorized payloads still fail.
