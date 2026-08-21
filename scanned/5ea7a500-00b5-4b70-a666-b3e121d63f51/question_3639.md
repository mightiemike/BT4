# Q3639: ExchangeProcessor: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeProcessor.exchangeFromSupply` in `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` — where the attacker uses ExchangeProcessor.exchangeFromSupply to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in ExchangeProcessor.exchangeFromSupply preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` -> `ExchangeProcessor.exchangeFromSupply`
- Entrypoint: broadcast exchange ops via ExchangeProcessor.exchangeFromSupply
- Attacker controls: request/transaction/contract inputs to `ExchangeProcessor.exchangeFromSupply` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses ExchangeProcessor.exchangeFromSupply to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in ExchangeProcessor.exchangeFromSupply preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
