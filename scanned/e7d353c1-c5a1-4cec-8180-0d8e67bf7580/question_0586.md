# Q586: ExchangeProcessor: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeProcessor.exchangeFromSupply` in `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` — where the attacker drives ExchangeProcessor.exchangeFromSupply usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in ExchangeProcessor.exchangeFromSupply never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` -> `ExchangeProcessor.exchangeFromSupply`
- Entrypoint: repeated ops via ExchangeProcessor.exchangeFromSupply
- Attacker controls: request/transaction/contract inputs to `ExchangeProcessor.exchangeFromSupply` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives ExchangeProcessor.exchangeFromSupply usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in ExchangeProcessor.exchangeFromSupply never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
