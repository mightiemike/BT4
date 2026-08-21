# Q535: ExchangeProcessor: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ExchangeProcessor.exchange` in `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` — where the attacker drives ExchangeProcessor.exchange usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in ExchangeProcessor.exchange never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java` -> `ExchangeProcessor.exchange`
- Entrypoint: repeated ops via ExchangeProcessor.exchange
- Attacker controls: request/transaction/contract inputs to `ExchangeProcessor.exchange` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives ExchangeProcessor.exchange usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in ExchangeProcessor.exchange never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
