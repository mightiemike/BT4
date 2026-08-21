# Q2433: MessageCall: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getEndowment` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker crafts a sequence reaching MessageCall.getEndowment where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in MessageCall.getEndowment, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getEndowment`
- Entrypoint: deploy/trigger a contract exercising MessageCall.getEndowment
- Attacker controls: request/transaction/contract inputs to `MessageCall.getEndowment` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching MessageCall.getEndowment where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in MessageCall.getEndowment
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
