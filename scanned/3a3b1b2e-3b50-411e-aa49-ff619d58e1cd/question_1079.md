# Q1079: InternalTransaction: memory expansion cost gap

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `InternalTransaction.reject` in `chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java` — where the attacker forces InternalTransaction.reject to expand memory/return-data past what its gas formula charges — to break the invariant that memory/copy operations charge quadratic cost matching allocation, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java` -> `InternalTransaction.reject`
- Entrypoint: contract hitting InternalTransaction.reject with large offsets
- Attacker controls: request/transaction/contract inputs to `InternalTransaction.reject` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: forces InternalTransaction.reject to expand memory/return-data past what its gas formula charges
- Invariant to test: memory/copy operations charge quadratic cost matching allocation
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test with huge offset asserting cost >= allocation
