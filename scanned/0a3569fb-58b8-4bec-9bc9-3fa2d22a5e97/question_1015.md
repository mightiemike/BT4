# Q1015: JumpTable: memory expansion cost gap

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `JumpTable.<primary method>` in `actuator/src/main/java/org/tron/core/vm/JumpTable.java` — where the attacker forces JumpTable.<primary method> to expand memory/return-data past what its gas formula charges — to break the invariant that memory/copy operations charge quadratic cost matching allocation, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/JumpTable.java` -> `JumpTable.<primary method>`
- Entrypoint: contract hitting JumpTable.<primary method> with large offsets
- Attacker controls: request/transaction/contract inputs to `JumpTable.<primary method>` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: forces JumpTable.<primary method> to expand memory/return-data past what its gas formula charges
- Invariant to test: memory/copy operations charge quadratic cost matching allocation
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test with huge offset asserting cost >= allocation
