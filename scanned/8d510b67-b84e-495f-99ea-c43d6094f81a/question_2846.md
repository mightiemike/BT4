# Q2846: Program: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Program.getPreviouslyExecutedOp` in `actuator/src/main/java/org/tron/core/vm/program/Program.java` — where the attacker crafts a sequence reaching Program.getPreviouslyExecutedOp where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in Program.getPreviouslyExecutedOp, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Program.java` -> `Program.getPreviouslyExecutedOp`
- Entrypoint: deploy/trigger a contract exercising Program.getPreviouslyExecutedOp
- Attacker controls: request/transaction/contract inputs to `Program.getPreviouslyExecutedOp` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching Program.getPreviouslyExecutedOp where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in Program.getPreviouslyExecutedOp
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
