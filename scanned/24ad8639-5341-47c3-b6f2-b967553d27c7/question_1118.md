# Q1118: Storage: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Storage.put` in `actuator/src/main/java/org/tron/core/vm/program/Storage.java` — where the attacker crafts a sequence reaching Storage.put where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in Storage.put, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Storage.java` -> `Storage.put`
- Entrypoint: deploy/trigger a contract exercising Storage.put
- Attacker controls: request/transaction/contract inputs to `Storage.put` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching Storage.put where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in Storage.put
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
