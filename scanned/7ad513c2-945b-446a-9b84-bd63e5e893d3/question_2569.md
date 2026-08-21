# Q2569: ProposalApproveActuator: duplicate/replay effect

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ProposalApproveActuator.execute` in `actuator/src/main/java/org/tron/core/actuator/ProposalApproveActuator.java` — where the attacker replays or batches ProposalApproveActuator exploiting a missing idempotency or index-key check to repeat its effect — to break the invariant that ProposalApproveActuator applies its effect exactly once per authorized intent, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/actuator/ProposalApproveActuator.java` -> `ProposalApproveActuator.execute`
- Entrypoint: broadcast ProposalApproveActuator twice with same/varied ids
- Attacker controls: request/transaction/contract inputs to `ProposalApproveActuator.execute` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: replays or batches ProposalApproveActuator exploiting a missing idempotency or index-key check to repeat its effect
- Invariant to test: ProposalApproveActuator applies its effect exactly once per authorized intent
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit executing ProposalApproveActuator twice and asserting single effect
