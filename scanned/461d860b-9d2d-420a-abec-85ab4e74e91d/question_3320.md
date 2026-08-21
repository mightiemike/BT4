# Q3320: VoteWitnessActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `VoteWitnessActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java` — where the attacker replays or batches VoteWitnessActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that VoteWitnessActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java` -> `VoteWitnessActuator.execute`
- Entrypoint: broadcast VoteWitnessActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `VoteWitnessActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches VoteWitnessActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: VoteWitnessActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing VoteWitnessActuator twice and asserting single effect
