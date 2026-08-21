# Q1657: VMActuator: owner/permission check gap

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VMActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/VMActuator.java` — where the attacker sets owner_address in VMActuator to an account they do not control, relying on VMActuator.validate not re-binding owner to the recovered signer — to break the invariant that the mutated account in VMActuator equals the account that authorized the signature, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/VMActuator.java` -> `VMActuator.validate`
- Entrypoint: broadcast VMActuator with foreign owner_address
- Attacker controls: request/transaction/contract inputs to `VMActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sets owner_address in VMActuator to an account they do not control, relying on VMActuator.validate not re-binding owner to the recovered signer
- Invariant to test: the mutated account in VMActuator equals the account that authorized the signature
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit signing with key A, owner B, assert validate rejects
