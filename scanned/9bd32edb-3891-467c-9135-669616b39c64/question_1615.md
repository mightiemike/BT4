# Q1615: cross-store atomicity bug in TransactionInfoCapsule.addFee

## Question
Can an unprivileged attacker use /jsonrpc eth_sendRawTransaction so chainbase/src/main/java/org/tron/core/capsule/TransactionInfoCapsule.java::addFee updates one store, index, or capsule successfully and another fails, leaving the system in a mixed atomicity state that leads to Unauthorized or duplicate settlement via transaction-processing confusion?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/TransactionInfoCapsule.java::addFee
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Look for flows where balances, indexes, receipts, reward state, and note state are written in separate steps without one all-or-nothing guard.
- Invariant to test: A public action that spans multiple stores must either commit all required writes or none of them.
- Expected Immunefi impact: Unauthorized or duplicate settlement via transaction-processing confusion
- Fast validation: Fault-inject failures after each individual write reachable from /jsonrpc eth_sendRawTransaction; assert no single-store commit can survive alone.
