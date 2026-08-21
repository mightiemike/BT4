# Q2424: StrictMathWrapper: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.floorDiv` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker supplies an input where StrictMathWrapper.floorDiv skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that StrictMathWrapper.floorDiv rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.floorDiv`
- Entrypoint: address string into StrictMathWrapper.floorDiv
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.floorDiv` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where StrictMathWrapper.floorDiv skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: StrictMathWrapper.floorDiv rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
