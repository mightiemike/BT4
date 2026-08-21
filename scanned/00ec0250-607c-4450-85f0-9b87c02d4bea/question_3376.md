# Q3376: UpdateAssetActuator: owner/permission check gap

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UpdateAssetActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java` — where the attacker sets owner_address in UpdateAssetActuator to an account they do not control, relying on UpdateAssetActuator.validate not re-binding owner to the recovered signer — to break the invariant that the mutated account in UpdateAssetActuator equals the account that authorized the signature, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java` -> `UpdateAssetActuator.validate`
- Entrypoint: broadcast UpdateAssetActuator with foreign owner_address
- Attacker controls: request/transaction/contract inputs to `UpdateAssetActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sets owner_address in UpdateAssetActuator to an account they do not control, relying on UpdateAssetActuator.validate not re-binding owner to the recovered signer
- Invariant to test: the mutated account in UpdateAssetActuator equals the account that authorized the signature
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit signing with key A, owner B, assert validate rejects
