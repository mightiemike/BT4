# Q3424: ForkController: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `ForkController.init` in `chainbase/src/main/java/org/tron/common/utils/ForkController.java` — where the attacker exploits ForkController.init to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that ForkController.init maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/ForkController.java` -> `ForkController.init`
- Entrypoint: input flowing into ForkController.init
- Attacker controls: request/transaction/contract inputs to `ForkController.init` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits ForkController.init to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: ForkController.init maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
