# Q2131: Memory: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Memory.readWord` in `actuator/src/main/java/org/tron/core/vm/program/Memory.java` — where the attacker crafts a sequence reaching Memory.readWord where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in Memory.readWord, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Memory.java` -> `Memory.readWord`
- Entrypoint: deploy/trigger a contract exercising Memory.readWord
- Attacker controls: request/transaction/contract inputs to `Memory.readWord` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching Memory.readWord where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in Memory.readWord
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
