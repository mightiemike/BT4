# Q3206: ForkController: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `ForkController.check` in `chainbase/src/main/java/org/tron/common/utils/ForkController.java` — where the attacker supplies an input where ForkController.check skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that ForkController.check rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/ForkController.java` -> `ForkController.check`
- Entrypoint: address string into ForkController.check
- Attacker controls: request/transaction/contract inputs to `ForkController.check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where ForkController.check skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: ForkController.check rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
