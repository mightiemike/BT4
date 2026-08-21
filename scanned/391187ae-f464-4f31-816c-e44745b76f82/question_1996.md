# Q1996: RLP: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `RLP.encodeLength` in `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` — where the attacker exploits RLP.encodeLength to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that RLP.encodeLength maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` -> `RLP.encodeLength`
- Entrypoint: input flowing into RLP.encodeLength
- Attacker controls: request/transaction/contract inputs to `RLP.encodeLength` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits RLP.encodeLength to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: RLP.encodeLength maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
