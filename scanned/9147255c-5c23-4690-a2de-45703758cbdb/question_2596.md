# Q2596: AbstractActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AbstractActuator.floorDiv` in `actuator/src/main/java/org/tron/core/actuator/AbstractActuator.java` — where the attacker replays or batches AbstractActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that AbstractActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/AbstractActuator.java` -> `AbstractActuator.floorDiv`
- Entrypoint: broadcast AbstractActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `AbstractActuator.floorDiv` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches AbstractActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: AbstractActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing AbstractActuator twice and asserting single effect
