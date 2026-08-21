# Q1555: StrictMathWrapper: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.addExact` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker supplies an input where StrictMathWrapper.addExact skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that StrictMathWrapper.addExact rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.addExact`
- Entrypoint: address string into StrictMathWrapper.addExact
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.addExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where StrictMathWrapper.addExact skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: StrictMathWrapper.addExact rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
