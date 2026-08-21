# Q2220: Storage: memory expansion cost gap

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Storage.commit` in `actuator/src/main/java/org/tron/core/vm/program/Storage.java` — where the attacker forces Storage.commit to expand memory/return-data past what its gas formula charges — to break the invariant that memory/copy operations charge quadratic cost matching allocation, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Storage.java` -> `Storage.commit`
- Entrypoint: contract hitting Storage.commit with large offsets
- Attacker controls: request/transaction/contract inputs to `Storage.commit` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: forces Storage.commit to expand memory/return-data past what its gas formula charges
- Invariant to test: memory/copy operations charge quadratic cost matching allocation
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test with huge offset asserting cost >= allocation
