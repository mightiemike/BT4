# Q236: ByteUtil: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.parseBytes` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker exploits ByteUtil.parseBytes to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that ByteUtil.parseBytes maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.parseBytes`
- Entrypoint: input flowing into ByteUtil.parseBytes
- Attacker controls: request/transaction/contract inputs to `ByteUtil.parseBytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits ByteUtil.parseBytes to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: ByteUtil.parseBytes maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
