# Q2170: ByteUtil: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.parseBytes` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker supplies an input where ByteUtil.parseBytes skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that ByteUtil.parseBytes rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.parseBytes`
- Entrypoint: address string into ByteUtil.parseBytes
- Attacker controls: request/transaction/contract inputs to `ByteUtil.parseBytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where ByteUtil.parseBytes skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: ByteUtil.parseBytes rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
