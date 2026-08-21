# Q3285: TransactionCapsule: mempool exhaustion

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionCapsule.getOwnerAddress` in `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` — where the attacker floods cheap transactions that TransactionCapsule.getOwnerAddress admits and holds, exhausting pending memory — to break the invariant that pending admission in TransactionCapsule.getOwnerAddress is bounded and cost-proportional, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java` -> `TransactionCapsule.getOwnerAddress`
- Entrypoint: flood pending via TransactionCapsule.getOwnerAddress
- Attacker controls: request/transaction/contract inputs to `TransactionCapsule.getOwnerAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: floods cheap transactions that TransactionCapsule.getOwnerAddress admits and holds, exhausting pending memory
- Invariant to test: pending admission in TransactionCapsule.getOwnerAddress is bounded and cost-proportional
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: load-test pending capacity asserting bound
