# Q1529: ProgramInvokeImpl: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `ProgramInvokeImpl.byTestingSuite` in `actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvokeImpl.java` — where the attacker crafts a sequence reaching ProgramInvokeImpl.byTestingSuite where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in ProgramInvokeImpl.byTestingSuite, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/invoke/ProgramInvokeImpl.java` -> `ProgramInvokeImpl.byTestingSuite`
- Entrypoint: deploy/trigger a contract exercising ProgramInvokeImpl.byTestingSuite
- Attacker controls: request/transaction/contract inputs to `ProgramInvokeImpl.byTestingSuite` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching ProgramInvokeImpl.byTestingSuite where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in ProgramInvokeImpl.byTestingSuite
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
