# Q305: ExchangeCreateActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeCreateActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java` — where the attacker replays or batches ExchangeCreateActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that ExchangeCreateActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java` -> `ExchangeCreateActuator.validate`
- Entrypoint: broadcast ExchangeCreateActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `ExchangeCreateActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches ExchangeCreateActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: ExchangeCreateActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing ExchangeCreateActuator twice and asserting single effect
