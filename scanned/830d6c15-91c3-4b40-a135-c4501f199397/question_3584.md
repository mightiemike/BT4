# Q3584: CompactEncoder: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `CompactEncoder.packNibbles` in `common/src/main/java/org/tron/common/utils/CompactEncoder.java` — where the attacker supplies an input where CompactEncoder.packNibbles skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that CompactEncoder.packNibbles rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/CompactEncoder.java` -> `CompactEncoder.packNibbles`
- Entrypoint: address string into CompactEncoder.packNibbles
- Attacker controls: request/transaction/contract inputs to `CompactEncoder.packNibbles` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where CompactEncoder.packNibbles skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: CompactEncoder.packNibbles rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
