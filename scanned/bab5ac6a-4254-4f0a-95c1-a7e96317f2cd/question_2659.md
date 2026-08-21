# Q2659: ExchangeProcessor: vote weight inflation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeProcessor.exchangeFromSupply` in `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` — where the attacker inflates vote weight through ExchangeProcessor.exchangeFromSupply beyond frozen stake — to break the invariant that votes counted in ExchangeProcessor.exchangeFromSupply never exceed the voter's frozen stake, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` -> `ExchangeProcessor.exchangeFromSupply`
- Entrypoint: broadcast votes via ExchangeProcessor.exchangeFromSupply
- Attacker controls: request/transaction/contract inputs to `ExchangeProcessor.exchangeFromSupply` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates vote weight through ExchangeProcessor.exchangeFromSupply beyond frozen stake
- Invariant to test: votes counted in ExchangeProcessor.exchangeFromSupply never exceed the voter's frozen stake
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit voting beyond stake asserting rejection
