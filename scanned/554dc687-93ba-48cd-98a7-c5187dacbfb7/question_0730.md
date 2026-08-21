# Q730: ExchangeInjectActuator: owner/permission check gap

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeInjectActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java` — where the attacker sets owner_address in ExchangeInjectActuator to an account they do not control, relying on ExchangeInjectActuator.validate not re-binding owner to the recovered signer — to break the invariant that the mutated account in ExchangeInjectActuator equals the account that authorized the signature, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java` -> `ExchangeInjectActuator.execute`
- Entrypoint: broadcast ExchangeInjectActuator with foreign owner_address
- Attacker controls: request/transaction/contract inputs to `ExchangeInjectActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sets owner_address in ExchangeInjectActuator to an account they do not control, relying on ExchangeInjectActuator.validate not re-binding owner to the recovered signer
- Invariant to test: the mutated account in ExchangeInjectActuator equals the account that authorized the signature
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit signing with key A, owner B, assert validate rejects
