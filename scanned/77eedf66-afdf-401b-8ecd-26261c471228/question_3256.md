# Q3256: Program: storage write miscount

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Program.setPreviouslyExecutedOp` in `actuator/src/main/java/org/tron/core/vm/program/Program.java` — where the attacker triggers Program.setPreviouslyExecutedOp so storage refunds/writes are counted wrong, letting free or negative-cost writes — to break the invariant that storage energy in Program.setPreviouslyExecutedOp equals net slots changed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Program.java` -> `Program.setPreviouslyExecutedOp`
- Entrypoint: contract toggling storage via Program.setPreviouslyExecutedOp
- Attacker controls: request/transaction/contract inputs to `Program.setPreviouslyExecutedOp` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Program.setPreviouslyExecutedOp so storage refunds/writes are counted wrong, letting free or negative-cost writes
- Invariant to test: storage energy in Program.setPreviouslyExecutedOp equals net slots changed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test toggling slots and asserting net charge
