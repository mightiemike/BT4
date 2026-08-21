# Q1143: Program: create2/address collision

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Program.setPreviouslyExecutedOp` in `actuator/src/main/java/org/tron/core/vm/program/Program.java` — where the attacker uses Program.setPreviouslyExecutedOp to deploy to a predicted address colliding with existing code/state — to break the invariant that address derivation in Program.setPreviouslyExecutedOp cannot overwrite existing contract state, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Program.java` -> `Program.setPreviouslyExecutedOp`
- Entrypoint: CREATE/CREATE2 via Program.setPreviouslyExecutedOp
- Attacker controls: request/transaction/contract inputs to `Program.setPreviouslyExecutedOp` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses Program.setPreviouslyExecutedOp to deploy to a predicted address colliding with existing code/state
- Invariant to test: address derivation in Program.setPreviouslyExecutedOp cannot overwrite existing contract state
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test colliding deploy asserting no overwrite
