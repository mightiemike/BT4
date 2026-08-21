# Q1636: ByteUtil: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.parseWord` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker supplies an input where ByteUtil.parseWord skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that ByteUtil.parseWord rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.parseWord`
- Entrypoint: address string into ByteUtil.parseWord
- Attacker controls: request/transaction/contract inputs to `ByteUtil.parseWord` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where ByteUtil.parseWord skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: ByteUtil.parseWord rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
