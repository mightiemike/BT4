# Q3636: Storage: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Storage.generateAddrHash` in `actuator/src/main/java/org/tron/core/vm/program/Storage.java` — where the attacker crafts a sequence reaching Storage.generateAddrHash where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in Storage.generateAddrHash, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Storage.java` -> `Storage.generateAddrHash`
- Entrypoint: deploy/trigger a contract exercising Storage.generateAddrHash
- Attacker controls: request/transaction/contract inputs to `Storage.generateAddrHash` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching Storage.generateAddrHash where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in Storage.generateAddrHash
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
