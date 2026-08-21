# Q1433: ByteUtil: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.bigIntegerToBytes` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker exploits ByteUtil.bigIntegerToBytes to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that ByteUtil.bigIntegerToBytes maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.bigIntegerToBytes`
- Entrypoint: input flowing into ByteUtil.bigIntegerToBytes
- Attacker controls: request/transaction/contract inputs to `ByteUtil.bigIntegerToBytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits ByteUtil.bigIntegerToBytes to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: ByteUtil.bigIntegerToBytes maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
