# Q2279: ExchangeWithdrawActuator: owner/permission check gap

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeWithdrawActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java` — where the attacker sets owner_address in ExchangeWithdrawActuator to an account they do not control, relying on ExchangeWithdrawActuator.validate not re-binding owner to the recovered signer — to break the invariant that the mutated account in ExchangeWithdrawActuator equals the account that authorized the signature, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java` -> `ExchangeWithdrawActuator.validate`
- Entrypoint: broadcast ExchangeWithdrawActuator with foreign owner_address
- Attacker controls: request/transaction/contract inputs to `ExchangeWithdrawActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sets owner_address in ExchangeWithdrawActuator to an account they do not control, relying on ExchangeWithdrawActuator.validate not re-binding owner to the recovered signer
- Invariant to test: the mutated account in ExchangeWithdrawActuator equals the account that authorized the signature
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit signing with key A, owner B, assert validate rejects
