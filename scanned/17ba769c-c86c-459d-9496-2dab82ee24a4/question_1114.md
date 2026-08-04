# Q1114: estimate-call bypass in InternalTransaction.getParentHash

## Question
Can an unprivileged attacker abuse gRPC broadcastTransaction so chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java::getParentHash does stateful or unusually expensive work in estimate/call mode that skips a production guard, giving the attacker a cheap public path to Materially underpriced public execution work or stateful simulation bug?

## Target
- File/function: chainbase/src/main/java/org/tron/common/runtime/InternalTransaction.java::getParentHash
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Compare constant-call and estimate paths against full execution to find missing resource, size, or validation checks.
- Invariant to test: Read-only and estimate paths must not mutate state, skip critical guards, or expose materially cheaper access to expensive execution.
- Expected Immunefi impact: Materially underpriced public execution work or stateful simulation bug
- Fast validation: Call the same contract via full execution and estimate/read-only routes; assert identical validation and no hidden side effects on the cheap path.
