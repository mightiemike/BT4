# Q3160: ByteUtil: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.parseWord` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker exploits ByteUtil.parseWord to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that ByteUtil.parseWord maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.parseWord`
- Entrypoint: input flowing into ByteUtil.parseWord
- Attacker controls: request/transaction/contract inputs to `ByteUtil.parseWord` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits ByteUtil.parseWord to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: ByteUtil.parseWord maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
