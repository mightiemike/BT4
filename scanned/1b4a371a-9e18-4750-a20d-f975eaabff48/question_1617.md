# Q1617: CompactEncoder: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `CompactEncoder.binToNibbles` in `common/src/main/java/org/tron/common/utils/CompactEncoder.java` — where the attacker exploits CompactEncoder.binToNibbles to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that CompactEncoder.binToNibbles maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/CompactEncoder.java` -> `CompactEncoder.binToNibbles`
- Entrypoint: input flowing into CompactEncoder.binToNibbles
- Attacker controls: request/transaction/contract inputs to `CompactEncoder.binToNibbles` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits CompactEncoder.binToNibbles to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: CompactEncoder.binToNibbles maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
