# Q1001: ExchangeWithdrawActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeWithdrawActuator.validate` in `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java` — where the attacker replays or batches ExchangeWithdrawActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that ExchangeWithdrawActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java` -> `ExchangeWithdrawActuator.validate`
- Entrypoint: broadcast ExchangeWithdrawActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `ExchangeWithdrawActuator.validate` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches ExchangeWithdrawActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: ExchangeWithdrawActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing ExchangeWithdrawActuator twice and asserting single effect
