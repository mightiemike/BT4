# Q3629: ByteArray: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteArray.toStr` in `common/src/main/java/org/tron/common/utils/ByteArray.java` — where the attacker exploits ByteArray.toStr to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that ByteArray.toStr maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteArray.java` -> `ByteArray.toStr`
- Entrypoint: input flowing into ByteArray.toStr
- Attacker controls: request/transaction/contract inputs to `ByteArray.toStr` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits ByteArray.toStr to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: ByteArray.toStr maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
