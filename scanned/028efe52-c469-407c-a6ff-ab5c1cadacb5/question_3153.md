# Q3153: Maths: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `Maths.min` in `common/src/main/java/org/tron/common/math/Maths.java` — where the attacker supplies an input where Maths.min skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that Maths.min rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/Maths.java` -> `Maths.min`
- Entrypoint: address string into Maths.min
- Attacker controls: request/transaction/contract inputs to `Maths.min` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where Maths.min skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: Maths.min rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
