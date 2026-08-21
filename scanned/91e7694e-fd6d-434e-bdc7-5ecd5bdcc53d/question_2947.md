# Q2947: UpdateEnergyLimitContractActuator: owner/permission check gap

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateEnergyLimitContractActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java` — where the attacker sets owner_address in UpdateEnergyLimitContractActuator to an account they do not control, relying on UpdateEnergyLimitContractActuator.validate not re-binding owner to the recovered signer — to break the invariant that the mutated account in UpdateEnergyLimitContractActuator equals the account that authorized the signature, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java` -> `UpdateEnergyLimitContractActuator.validate`
- Entrypoint: broadcast UpdateEnergyLimitContractActuator with foreign owner_address
- Attacker controls: request/transaction/contract inputs to `UpdateEnergyLimitContractActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sets owner_address in UpdateEnergyLimitContractActuator to an account they do not control, relying on UpdateEnergyLimitContractActuator.validate not re-binding owner to the recovered signer
- Invariant to test: the mutated account in UpdateEnergyLimitContractActuator equals the account that authorized the signature
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit signing with key A, owner B, assert validate rejects
