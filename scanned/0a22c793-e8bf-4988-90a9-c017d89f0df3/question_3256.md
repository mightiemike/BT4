# Q3256: serialization collision in TxInputUtil.newTxInput

## Question
Can an unprivileged attacker craft values through gRPC broadcastTransaction so framework/src/main/java/org/tron/core/capsule/utils/TxInputUtil.java::newTxInput serializes two distinct logical objects to the same internal key or byte layout, causing wrong-object reads/writes and leading to Unauthorized transaction execution or state mutation?

## Target
- File/function: framework/src/main/java/org/tron/core/capsule/utils/TxInputUtil.java::newTxInput
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Probe padding, truncation, version bytes, composite keys, and alternate encodings of the same public identifier.
- Invariant to test: Every logical object must serialize to one unique key and deserialize back to the same object without aliasing.
- Expected Immunefi impact: Unauthorized transaction execution or state mutation
- Fast validation: Generate colliding candidate identifiers via gRPC broadcastTransaction; assert they never read or overwrite another live record.
