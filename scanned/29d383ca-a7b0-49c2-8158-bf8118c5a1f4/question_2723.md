# Q2723: CompactEncoder: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `CompactEncoder.packNibbles` in `common/src/main/java/org/tron/common/utils/CompactEncoder.java` — where the attacker exploits CompactEncoder.packNibbles to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that CompactEncoder.packNibbles maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/CompactEncoder.java` -> `CompactEncoder.packNibbles`
- Entrypoint: input flowing into CompactEncoder.packNibbles
- Attacker controls: request/transaction/contract inputs to `CompactEncoder.packNibbles` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits CompactEncoder.packNibbles to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: CompactEncoder.packNibbles maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
