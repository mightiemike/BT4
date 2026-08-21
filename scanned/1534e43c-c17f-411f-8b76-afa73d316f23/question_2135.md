# Q2135: ResourceProcessor: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ResourceProcessor.calculateGlobalLimitV2` in `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` — where the attacker drives ResourceProcessor.calculateGlobalLimitV2 usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in ResourceProcessor.calculateGlobalLimitV2 never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` -> `ResourceProcessor.calculateGlobalLimitV2`
- Entrypoint: repeated ops via ResourceProcessor.calculateGlobalLimitV2
- Attacker controls: request/transaction/contract inputs to `ResourceProcessor.calculateGlobalLimitV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives ResourceProcessor.calculateGlobalLimitV2 usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in ResourceProcessor.calculateGlobalLimitV2 never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
