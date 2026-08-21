# Q2450: CommonParameter: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `CommonParameter.reset` in `common/src/main/java/org/tron/common/parameter/CommonParameter.java` — where the attacker exploits CommonParameter.reset to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that CommonParameter.reset maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/parameter/CommonParameter.java` -> `CommonParameter.reset`
- Entrypoint: input flowing into CommonParameter.reset
- Attacker controls: request/transaction/contract inputs to `CommonParameter.reset` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits CommonParameter.reset to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: CommonParameter.reset maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
