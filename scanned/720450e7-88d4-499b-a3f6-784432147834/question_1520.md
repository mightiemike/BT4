# Q1520: CompactEncoder: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `CompactEncoder.binToNibblesNoTerminator` in `common/src/main/java/org/tron/common/utils/CompactEncoder.java` — where the attacker exploits CompactEncoder.binToNibblesNoTerminator to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that CompactEncoder.binToNibblesNoTerminator maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/CompactEncoder.java` -> `CompactEncoder.binToNibblesNoTerminator`
- Entrypoint: input flowing into CompactEncoder.binToNibblesNoTerminator
- Attacker controls: request/transaction/contract inputs to `CompactEncoder.binToNibblesNoTerminator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits CompactEncoder.binToNibblesNoTerminator to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: CompactEncoder.binToNibblesNoTerminator maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
