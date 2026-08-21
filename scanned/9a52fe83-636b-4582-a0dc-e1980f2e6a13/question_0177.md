# Q177: MessageCall: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getOpCode` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker crafts a sequence reaching MessageCall.getOpCode where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in MessageCall.getOpCode, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getOpCode`
- Entrypoint: deploy/trigger a contract exercising MessageCall.getOpCode
- Attacker controls: request/transaction/contract inputs to `MessageCall.getOpCode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching MessageCall.getOpCode where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in MessageCall.getOpCode
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
