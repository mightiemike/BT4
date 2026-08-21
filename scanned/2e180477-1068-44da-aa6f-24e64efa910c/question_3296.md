# Q3296: RLP: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `RLP.encodeByte` in `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` — where the attacker exploits RLP.encodeByte to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that RLP.encodeByte maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` -> `RLP.encodeByte`
- Entrypoint: input flowing into RLP.encodeByte
- Attacker controls: request/transaction/contract inputs to `RLP.encodeByte` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits RLP.encodeByte to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: RLP.encodeByte maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
