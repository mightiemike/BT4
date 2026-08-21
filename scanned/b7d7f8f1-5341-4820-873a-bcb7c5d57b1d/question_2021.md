# Q2021: MUtil: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MUtil.checkCPUTime` in `actuator/src/main/java/org/tron/core/vm/utils/MUtil.java` — where the attacker crafts a sequence reaching MUtil.checkCPUTime where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in MUtil.checkCPUTime, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/utils/MUtil.java` -> `MUtil.checkCPUTime`
- Entrypoint: deploy/trigger a contract exercising MUtil.checkCPUTime
- Attacker controls: request/transaction/contract inputs to `MUtil.checkCPUTime` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching MUtil.checkCPUTime where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in MUtil.checkCPUTime
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
