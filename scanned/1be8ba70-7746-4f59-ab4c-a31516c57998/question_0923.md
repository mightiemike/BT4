# Q923: Maths: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `Maths.floorDiv` in `common/src/main/java/org/tron/common/math/Maths.java` — where the attacker exploits Maths.floorDiv to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that Maths.floorDiv maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/Maths.java` -> `Maths.floorDiv`
- Entrypoint: input flowing into Maths.floorDiv
- Attacker controls: request/transaction/contract inputs to `Maths.floorDiv` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits Maths.floorDiv to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: Maths.floorDiv maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
