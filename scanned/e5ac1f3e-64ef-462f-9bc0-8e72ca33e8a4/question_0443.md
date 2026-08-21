# Q443: StrictMathWrapper: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.multiplyExact` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker supplies an input where StrictMathWrapper.multiplyExact skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that StrictMathWrapper.multiplyExact rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.multiplyExact`
- Entrypoint: address string into StrictMathWrapper.multiplyExact
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.multiplyExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where StrictMathWrapper.multiplyExact skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: StrictMathWrapper.multiplyExact rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
