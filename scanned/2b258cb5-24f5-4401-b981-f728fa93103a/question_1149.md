# Q1149: TransactionCapsule: mempool exhaustion

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionCapsule.getToAddress` in `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` — where the attacker floods cheap transactions that TransactionCapsule.getToAddress admits and holds, exhausting pending memory — to break the invariant that pending admission in TransactionCapsule.getToAddress is bounded and cost-proportional, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` -> `TransactionCapsule.getToAddress`
- Entrypoint: flood pending via TransactionCapsule.getToAddress
- Attacker controls: request/transaction/contract inputs to `TransactionCapsule.getToAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: floods cheap transactions that TransactionCapsule.getToAddress admits and holds, exhausting pending memory
- Invariant to test: pending admission in TransactionCapsule.getToAddress is bounded and cost-proportional
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: load-test pending capacity asserting bound
