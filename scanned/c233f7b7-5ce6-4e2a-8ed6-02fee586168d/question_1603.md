# Q1603: ForkController: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `ForkController.passOld` in `chainbase/src/main/java/org/tron/common/utils/ForkController.java` — where the attacker exploits ForkController.passOld to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that ForkController.passOld maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/ForkController.java` -> `ForkController.passOld`
- Entrypoint: input flowing into ForkController.passOld
- Attacker controls: request/transaction/contract inputs to `ForkController.passOld` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits ForkController.passOld to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: ForkController.passOld maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
