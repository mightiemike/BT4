# Q1339: cross-store atomicity bug in AssetIssueCapsule.createDbKeyString

## Question
Can an unprivileged attacker use gRPC createTransaction2 -> broadcastTransaction so chainbase/src/main/java/org/tron/core/capsule/AssetIssueCapsule.java::createDbKeyString updates one store, index, or capsule successfully and another fails, leaving the system in a mixed atomicity state that leads to Unauthorized transfer or minting of TRX/TRC10 value?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/AssetIssueCapsule.java::createDbKeyString
- Entrypoint: gRPC createTransaction2 -> broadcastTransaction
- Attacker controls: owner/to addresses, amount, asset id, permission_id, signatures, and visible/base58/hex encoding
- Exploit idea: Look for flows where balances, indexes, receipts, reward state, and note state are written in separate steps without one all-or-nothing guard.
- Invariant to test: A public action that spans multiple stores must either commit all required writes or none of them.
- Expected Immunefi impact: Unauthorized transfer or minting of TRX/TRC10 value
- Fast validation: Fault-inject failures after each individual write reachable from gRPC createTransaction2 -> broadcastTransaction; assert no single-store commit can survive alone.
