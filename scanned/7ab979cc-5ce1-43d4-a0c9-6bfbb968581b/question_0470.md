# Q470: ResourceProcessor: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ResourceProcessor.unDelegateIncreaseV2` in `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` — where the attacker drives ResourceProcessor.unDelegateIncreaseV2 usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in ResourceProcessor.unDelegateIncreaseV2 never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` -> `ResourceProcessor.unDelegateIncreaseV2`
- Entrypoint: repeated ops via ResourceProcessor.unDelegateIncreaseV2
- Attacker controls: request/transaction/contract inputs to `ResourceProcessor.unDelegateIncreaseV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives ResourceProcessor.unDelegateIncreaseV2 usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in ResourceProcessor.unDelegateIncreaseV2 never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
