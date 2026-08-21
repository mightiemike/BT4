# Q1522: AbstractExchangeActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AbstractExchangeActuator.addExact` in `actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java` — where the attacker replays or batches AbstractExchangeActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that AbstractExchangeActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java` -> `AbstractExchangeActuator.addExact`
- Entrypoint: broadcast AbstractExchangeActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `AbstractExchangeActuator.addExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches AbstractExchangeActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: AbstractExchangeActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing AbstractExchangeActuator twice and asserting single effect
