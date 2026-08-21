# Q2716: CompactEncoder: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `CompactEncoder.binToNibbles` in `common/src/main/java/org/tron/common/utils/CompactEncoder.java` — where the attacker supplies an input where CompactEncoder.binToNibbles skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that CompactEncoder.binToNibbles rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/CompactEncoder.java` -> `CompactEncoder.binToNibbles`
- Entrypoint: address string into CompactEncoder.binToNibbles
- Attacker controls: request/transaction/contract inputs to `CompactEncoder.binToNibbles` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where CompactEncoder.binToNibbles skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: CompactEncoder.binToNibbles rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
