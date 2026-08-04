# Q3199: cross-store atomicity bug in DecodeResult.getDecoded

## Question
Can an unprivileged attacker use /jsonrpc eth_sendRawTransaction so framework/src/main/java/org/tron/core/capsule/utils/DecodeResult.java::getDecoded updates one store, index, or capsule successfully and another fails, leaving the system in a mixed atomicity state that leads to Unauthorized transaction execution or state mutation?

## Target
- File/function: framework/src/main/java/org/tron/core/capsule/utils/DecodeResult.java::getDecoded
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Look for flows where balances, indexes, receipts, reward state, and note state are written in separate steps without one all-or-nothing guard.
- Invariant to test: A public action that spans multiple stores must either commit all required writes or none of them.
- Expected Immunefi impact: Unauthorized transaction execution or state mutation
- Fast validation: Fault-inject failures after each individual write reachable from /jsonrpc eth_sendRawTransaction; assert no single-store commit can survive alone.
