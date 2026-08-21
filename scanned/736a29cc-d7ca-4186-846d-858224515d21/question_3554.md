# Q3554: ExchangeTransactionActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeTransactionActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java` — where the attacker replays or batches ExchangeTransactionActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that ExchangeTransactionActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java` -> `ExchangeTransactionActuator.validate`
- Entrypoint: broadcast ExchangeTransactionActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `ExchangeTransactionActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches ExchangeTransactionActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: ExchangeTransactionActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing ExchangeTransactionActuator twice and asserting single effect
