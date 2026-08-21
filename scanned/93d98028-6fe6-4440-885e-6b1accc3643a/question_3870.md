# Q3870: ExchangeProcessor: stale price/window read

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeProcessor.exchangeToSupply` in `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` — where the attacker times ExchangeProcessor.exchangeToSupply to read a stale energy/bandwidth price or usage window, underpaying for real work — to break the invariant that ExchangeProcessor.exchangeToSupply reads the current price/window at charge time, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` -> `ExchangeProcessor.exchangeToSupply`
- Entrypoint: broadcast metered by ExchangeProcessor.exchangeToSupply across a window boundary
- Attacker controls: request/transaction/contract inputs to `ExchangeProcessor.exchangeToSupply` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times ExchangeProcessor.exchangeToSupply to read a stale energy/bandwidth price or usage window, underpaying for real work
- Invariant to test: ExchangeProcessor.exchangeToSupply reads the current price/window at charge time
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit at price/window boundary asserting current value used
