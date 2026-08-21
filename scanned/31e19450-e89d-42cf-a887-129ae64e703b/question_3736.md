# Q3736: ByteUtil: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.intToBytesNoLeadZeroes` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker supplies an input where ByteUtil.intToBytesNoLeadZeroes skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that ByteUtil.intToBytesNoLeadZeroes rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.intToBytesNoLeadZeroes`
- Entrypoint: address string into ByteUtil.intToBytesNoLeadZeroes
- Attacker controls: request/transaction/contract inputs to `ByteUtil.intToBytesNoLeadZeroes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where ByteUtil.intToBytesNoLeadZeroes skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: ByteUtil.intToBytesNoLeadZeroes rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
