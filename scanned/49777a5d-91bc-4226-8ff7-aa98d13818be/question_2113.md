# Q2113: WithdrawBalanceActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `WithdrawBalanceActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java` — where the attacker replays or batches WithdrawBalanceActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that WithdrawBalanceActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java` -> `WithdrawBalanceActuator.execute`
- Entrypoint: broadcast WithdrawBalanceActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `WithdrawBalanceActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches WithdrawBalanceActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: WithdrawBalanceActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing WithdrawBalanceActuator twice and asserting single effect
