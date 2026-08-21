# Q1969: ExchangeProcessor: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeProcessor.exchangeToSupply` in `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` — where the attacker drives ExchangeProcessor.exchangeToSupply usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in ExchangeProcessor.exchangeToSupply never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` -> `ExchangeProcessor.exchangeToSupply`
- Entrypoint: repeated ops via ExchangeProcessor.exchangeToSupply
- Attacker controls: request/transaction/contract inputs to `ExchangeProcessor.exchangeToSupply` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives ExchangeProcessor.exchangeToSupply usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in ExchangeProcessor.exchangeToSupply never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
