# Q2431: Stack: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Stack.pop` in `actuator/src/main/java/org/tron/core/vm/program/Stack.java` — where the attacker crafts a sequence reaching Stack.pop where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in Stack.pop, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Stack.java` -> `Stack.pop`
- Entrypoint: deploy/trigger a contract exercising Stack.pop
- Attacker controls: request/transaction/contract inputs to `Stack.pop` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching Stack.pop where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in Stack.pop
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
