# Q3094: ForkController: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `ForkController.passNew` in `chainbase/src/main/java/org/tron/common/utils/ForkController.java` — where the attacker exploits ForkController.passNew to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that ForkController.passNew maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/ForkController.java` -> `ForkController.passNew`
- Entrypoint: input flowing into ForkController.passNew
- Attacker controls: request/transaction/contract inputs to `ForkController.passNew` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits ForkController.passNew to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: ForkController.passNew maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
