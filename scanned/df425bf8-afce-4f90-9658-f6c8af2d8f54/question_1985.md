# Q1985: TransactionCache: mempool exhaustion

## Question
Can an unprivileged attacker (broadcast transaction) abuse `TransactionCache.initCache` in `chainbase/src/main/java/org/tron/core/db/TransactionCache.java` — where the attacker floods cheap transactions that TransactionCache.initCache admits and holds, exhausting pending memory — to break the invariant that pending admission in TransactionCache.initCache is bounded and cost-proportional, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TransactionCache.java` -> `TransactionCache.initCache`
- Entrypoint: flood pending via TransactionCache.initCache
- Attacker controls: request/transaction/contract inputs to `TransactionCache.initCache` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: floods cheap transactions that TransactionCache.initCache admits and holds, exhausting pending memory
- Invariant to test: pending admission in TransactionCache.initCache is bounded and cost-proportional
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: load-test pending capacity asserting bound
