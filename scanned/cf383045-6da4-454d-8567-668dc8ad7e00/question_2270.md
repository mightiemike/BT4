# Q2270: StrictMathWrapper: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.addExact` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker exploits StrictMathWrapper.addExact to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that StrictMathWrapper.addExact maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.addExact`
- Entrypoint: input flowing into StrictMathWrapper.addExact
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.addExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits StrictMathWrapper.addExact to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: StrictMathWrapper.addExact maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
