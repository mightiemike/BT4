# Q1720: serialization collision in TransactionUtil.newGenesisTransaction

## Question
Can an unprivileged attacker craft values through /wallet/broadcasthex so chainbase/src/main/java/org/tron/core/capsule/utils/TransactionUtil.java::newGenesisTransaction serializes two distinct logical objects to the same internal key or byte layout, causing wrong-object reads/writes and leading to Unauthorized or duplicate settlement via transaction-processing confusion?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/utils/TransactionUtil.java::newGenesisTransaction
- Entrypoint: /wallet/broadcasthex
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Probe padding, truncation, version bytes, composite keys, and alternate encodings of the same public identifier.
- Invariant to test: Every logical object must serialize to one unique key and deserialize back to the same object without aliasing.
- Expected Immunefi impact: Unauthorized or duplicate settlement via transaction-processing confusion
- Fast validation: Generate colliding candidate identifiers via /wallet/broadcasthex; assert they never read or overwrite another live record.
