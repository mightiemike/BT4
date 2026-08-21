# Q2306: MessageCall: memory expansion cost gap

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getCodeAddress` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker forces MessageCall.getCodeAddress to expand memory/return-data past what its gas formula charges — to break the invariant that memory/copy operations charge quadratic cost matching allocation, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getCodeAddress`
- Entrypoint: contract hitting MessageCall.getCodeAddress with large offsets
- Attacker controls: request/transaction/contract inputs to `MessageCall.getCodeAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: forces MessageCall.getCodeAddress to expand memory/return-data past what its gas formula charges
- Invariant to test: memory/copy operations charge quadratic cost matching allocation
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test with huge offset asserting cost >= allocation
