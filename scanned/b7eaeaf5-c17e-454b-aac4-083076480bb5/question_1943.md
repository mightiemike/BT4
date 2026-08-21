# Q1943: ExchangeInjectActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeInjectActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java` — where the attacker replays or batches ExchangeInjectActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that ExchangeInjectActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java` -> `ExchangeInjectActuator.execute`
- Entrypoint: broadcast ExchangeInjectActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `ExchangeInjectActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches ExchangeInjectActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: ExchangeInjectActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing ExchangeInjectActuator twice and asserting single effect
