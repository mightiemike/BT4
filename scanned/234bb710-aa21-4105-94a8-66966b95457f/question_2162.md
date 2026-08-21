# Q2162: StrictMathWrapper: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.subtractExact` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker exploits StrictMathWrapper.subtractExact to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that StrictMathWrapper.subtractExact maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.subtractExact`
- Entrypoint: input flowing into StrictMathWrapper.subtractExact
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.subtractExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits StrictMathWrapper.subtractExact to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: StrictMathWrapper.subtractExact maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
