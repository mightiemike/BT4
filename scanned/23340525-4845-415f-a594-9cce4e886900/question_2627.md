# Q2627: ForkController: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `ForkController.passNew` in `chainbase/src/main/java/org/tron/common/utils/ForkController.java` — where the attacker supplies an input where ForkController.passNew skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that ForkController.passNew rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/ForkController.java` -> `ForkController.passNew`
- Entrypoint: address string into ForkController.passNew
- Attacker controls: request/transaction/contract inputs to `ForkController.passNew` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where ForkController.passNew skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: ForkController.passNew rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
