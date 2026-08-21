# Q728: SafeExchangeProcessor: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `SafeExchangeProcessor.exchange` in `chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java` — where the attacker drives SafeExchangeProcessor.exchange usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in SafeExchangeProcessor.exchange never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java` -> `SafeExchangeProcessor.exchange`
- Entrypoint: repeated ops via SafeExchangeProcessor.exchange
- Attacker controls: request/transaction/contract inputs to `SafeExchangeProcessor.exchange` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives SafeExchangeProcessor.exchange usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in SafeExchangeProcessor.exchange never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
