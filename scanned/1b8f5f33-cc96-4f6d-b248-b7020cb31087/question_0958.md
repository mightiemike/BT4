# Q958: MarketOrderStore: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `MarketOrderStore.<primary method>` in `chainbase/src/main/java/org/tron/core/store/MarketOrderStore.java` — where the attacker drives MarketOrderStore.<primary method> usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in MarketOrderStore.<primary method> never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/MarketOrderStore.java` -> `MarketOrderStore.<primary method>`
- Entrypoint: repeated ops via MarketOrderStore.<primary method>
- Attacker controls: request/transaction/contract inputs to `MarketOrderStore.<primary method>` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives MarketOrderStore.<primary method> usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in MarketOrderStore.<primary method> never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
