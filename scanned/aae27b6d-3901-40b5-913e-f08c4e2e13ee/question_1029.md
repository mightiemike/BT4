# Q1029: ByteArray: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteArray.fromHexString` in `common/src/main/java/org/tron/common/utils/ByteArray.java` — where the attacker exploits ByteArray.fromHexString to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that ByteArray.fromHexString maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteArray.java` -> `ByteArray.fromHexString`
- Entrypoint: input flowing into ByteArray.fromHexString
- Attacker controls: request/transaction/contract inputs to `ByteArray.fromHexString` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits ByteArray.fromHexString to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: ByteArray.fromHexString maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
