# Q2091: ParticipateAssetIssueActuator: owner/permission check gap

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ParticipateAssetIssueActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java` — where the attacker sets owner_address in ParticipateAssetIssueActuator to an account they do not control, relying on ParticipateAssetIssueActuator.validate not re-binding owner to the recovered signer — to break the invariant that the mutated account in ParticipateAssetIssueActuator equals the account that authorized the signature, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java` -> `ParticipateAssetIssueActuator.validate`
- Entrypoint: broadcast ParticipateAssetIssueActuator with foreign owner_address
- Attacker controls: request/transaction/contract inputs to `ParticipateAssetIssueActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sets owner_address in ParticipateAssetIssueActuator to an account they do not control, relying on ParticipateAssetIssueActuator.validate not re-binding owner to the recovered signer
- Invariant to test: the mutated account in ParticipateAssetIssueActuator equals the account that authorized the signature
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit signing with key A, owner B, assert validate rejects
