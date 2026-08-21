# Q3075: ExchangeProcessor: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeProcessor.exchange` in `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` — where the attacker uses ExchangeProcessor.exchange to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in ExchangeProcessor.exchange preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` -> `ExchangeProcessor.exchange`
- Entrypoint: broadcast exchange ops via ExchangeProcessor.exchange
- Attacker controls: request/transaction/contract inputs to `ExchangeProcessor.exchange` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses ExchangeProcessor.exchange to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in ExchangeProcessor.exchange preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
