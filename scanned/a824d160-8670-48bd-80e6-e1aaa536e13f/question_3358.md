# Q3358: Maths: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `Maths.pow` in `common/src/main/java/org/tron/common/math/Maths.java` — where the attacker exploits Maths.pow to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that Maths.pow maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/Maths.java` -> `Maths.pow`
- Entrypoint: input flowing into Maths.pow
- Attacker controls: request/transaction/contract inputs to `Maths.pow` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits Maths.pow to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: Maths.pow maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
