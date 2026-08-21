# Q3027: RepositoryImpl: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `RepositoryImpl.newRepositoryChild` in `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java` — where the attacker crafts a sequence reaching RepositoryImpl.newRepositoryChild where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in RepositoryImpl.newRepositoryChild, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java` -> `RepositoryImpl.newRepositoryChild`
- Entrypoint: deploy/trigger a contract exercising RepositoryImpl.newRepositoryChild
- Attacker controls: request/transaction/contract inputs to `RepositoryImpl.newRepositoryChild` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching RepositoryImpl.newRepositoryChild where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in RepositoryImpl.newRepositoryChild
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
