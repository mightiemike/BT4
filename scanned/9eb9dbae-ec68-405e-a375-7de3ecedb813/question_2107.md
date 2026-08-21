# Q2107: CommonParameter: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `CommonParameter.calcMaxTimeRatio` in `common/src/main/java/org/tron/common/parameter/CommonParameter.java` — where the attacker exploits CommonParameter.calcMaxTimeRatio to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that CommonParameter.calcMaxTimeRatio maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `common/src/main/java/org/tron/common/parameter/CommonParameter.java` -> `CommonParameter.calcMaxTimeRatio`
- Entrypoint: input flowing into CommonParameter.calcMaxTimeRatio
- Attacker controls: request/transaction/contract inputs to `CommonParameter.calcMaxTimeRatio` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits CommonParameter.calcMaxTimeRatio to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: CommonParameter.calcMaxTimeRatio maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
