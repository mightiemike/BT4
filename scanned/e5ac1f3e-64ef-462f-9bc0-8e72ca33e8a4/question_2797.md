# Q2797: UnDelegateResourceActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `UnDelegateResourceActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java` — where the attacker replays or batches UnDelegateResourceActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that UnDelegateResourceActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java` -> `UnDelegateResourceActuator.execute`
- Entrypoint: broadcast UnDelegateResourceActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `UnDelegateResourceActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches UnDelegateResourceActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: UnDelegateResourceActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing UnDelegateResourceActuator twice and asserting single effect
