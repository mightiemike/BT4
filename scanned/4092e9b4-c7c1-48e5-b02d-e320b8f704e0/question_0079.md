# Q79: InternalTransaction: energy metering mismatch

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `InternalTransaction.reject` in `chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java` — where the attacker crafts a sequence reaching InternalTransaction.reject where charged energy diverges from actual CPU/memory work, or is charged after the work — to break the invariant that energy is charged before or exactly in step with the work in InternalTransaction.reject, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java` -> `InternalTransaction.reject`
- Entrypoint: deploy/trigger a contract exercising InternalTransaction.reject
- Attacker controls: request/transaction/contract inputs to `InternalTransaction.reject` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a sequence reaching InternalTransaction.reject where charged energy diverges from actual CPU/memory work, or is charged after the work
- Invariant to test: energy is charged before or exactly in step with the work in InternalTransaction.reject
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test comparing charged energy to executed steps
