# Q2016: MessageCall: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getInDataSize` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker crafts a sequence reaching MessageCall.getInDataSize where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in MessageCall.getInDataSize, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getInDataSize`
- Entrypoint: deploy/trigger a contract exercising MessageCall.getInDataSize
- Attacker controls: request/transaction/contract inputs to `MessageCall.getInDataSize` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching MessageCall.getInDataSize where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in MessageCall.getInDataSize
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
