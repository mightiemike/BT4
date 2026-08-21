# Q1317: InternalTransaction: call value/token reentrancy

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `InternalTransaction.reject` in `chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java` — where the attacker reenters InternalTransaction.reject using a contract they control during a value/TRC10 transfer so balance is read before it is debited — to break the invariant that InternalTransaction.reject debits before yielding control to callee, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java` -> `InternalTransaction.reject`
- Entrypoint: reentrant contract exercising InternalTransaction.reject
- Attacker controls: request/transaction/contract inputs to `InternalTransaction.reject` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: reenters InternalTransaction.reject using a contract they control during a value/TRC10 transfer so balance is read before it is debited
- Invariant to test: InternalTransaction.reject debits before yielding control to callee
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test reentering on transfer asserting single debit
