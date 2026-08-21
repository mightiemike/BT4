# Q702: StrictMathWrapper: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.multiplyExact` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker exploits StrictMathWrapper.multiplyExact to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that StrictMathWrapper.multiplyExact maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.multiplyExact`
- Entrypoint: input flowing into StrictMathWrapper.multiplyExact
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.multiplyExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits StrictMathWrapper.multiplyExact to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: StrictMathWrapper.multiplyExact maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
