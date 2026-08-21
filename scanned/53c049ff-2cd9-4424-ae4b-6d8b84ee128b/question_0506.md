# Q506: InternalTransaction: stack/depth bound bypass

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `InternalTransaction.reject` in `chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java` — where the attacker recurses or grows stack via InternalTransaction.reject past the depth/size bound without proportional cost — to break the invariant that InternalTransaction.reject enforces call depth and stack size before work, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java` -> `InternalTransaction.reject`
- Entrypoint: deeply nested call reaching InternalTransaction.reject
- Attacker controls: request/transaction/contract inputs to `InternalTransaction.reject` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: recurses or grows stack via InternalTransaction.reject past the depth/size bound without proportional cost
- Invariant to test: InternalTransaction.reject enforces call depth and stack size before work
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: VM test at depth boundary asserting revert not hang
