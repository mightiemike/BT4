# Q2867: MessageCall: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getCodeAddress` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker crafts a sequence reaching MessageCall.getCodeAddress where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in MessageCall.getCodeAddress, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getCodeAddress`
- Entrypoint: deploy/trigger a contract exercising MessageCall.getCodeAddress
- Attacker controls: request/transaction/contract inputs to `MessageCall.getCodeAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching MessageCall.getCodeAddress where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in MessageCall.getCodeAddress
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
