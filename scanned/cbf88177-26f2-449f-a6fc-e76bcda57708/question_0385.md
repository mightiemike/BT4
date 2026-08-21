# Q385: ExchangeProcessor: bandwidth free-ride

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeProcessor.exchange` in `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` — where the attacker shapes usage so ExchangeProcessor.exchange charges zero or stale bandwidth for real transactions — to break the invariant that every metered transaction pays current bandwidth in ExchangeProcessor.exchange, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` -> `ExchangeProcessor.exchange`
- Entrypoint: broadcast txs metered by ExchangeProcessor.exchange
- Attacker controls: request/transaction/contract inputs to `ExchangeProcessor.exchange` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: shapes usage so ExchangeProcessor.exchange charges zero or stale bandwidth for real transactions
- Invariant to test: every metered transaction pays current bandwidth in ExchangeProcessor.exchange
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit asserting bandwidth decremented per tx
