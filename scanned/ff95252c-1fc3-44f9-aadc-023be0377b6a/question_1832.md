# Q1832: ExchangeProcessor: stale price/window read

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeProcessor.exchangeFromSupply` in `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` — where the attacker times ExchangeProcessor.exchangeFromSupply to read a stale energy/bandwidth price or usage window, underpaying for real work — to break the invariant that ExchangeProcessor.exchangeFromSupply reads the current price/window at charge time, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` -> `ExchangeProcessor.exchangeFromSupply`
- Entrypoint: broadcast metered by ExchangeProcessor.exchangeFromSupply across a window boundary
- Attacker controls: request/transaction/contract inputs to `ExchangeProcessor.exchangeFromSupply` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: times ExchangeProcessor.exchangeFromSupply to read a stale energy/bandwidth price or usage window, underpaying for real work
- Invariant to test: ExchangeProcessor.exchangeFromSupply reads the current price/window at charge time
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit at price/window boundary asserting current value used
