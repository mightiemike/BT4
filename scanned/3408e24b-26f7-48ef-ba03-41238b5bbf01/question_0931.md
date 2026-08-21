# Q931: VM: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VM.play` in `actuator/src/main/java/org/tron/core/vm/VM.java` — where the attacker crafts a sequence reaching VM.play where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in VM.play, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/VM.java` -> `VM.play`
- Entrypoint: deploy/trigger a contract exercising VM.play
- Attacker controls: request/transaction/contract inputs to `VM.play` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching VM.play where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in VM.play
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
