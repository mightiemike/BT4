# Q2940: ResourceProcessor: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ResourceProcessor.unDelegateIncrease` in `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` — where the attacker drives ResourceProcessor.unDelegateIncrease usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in ResourceProcessor.unDelegateIncrease never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java` -> `ResourceProcessor.unDelegateIncrease`
- Entrypoint: repeated ops via ResourceProcessor.unDelegateIncrease
- Attacker controls: request/transaction/contract inputs to `ResourceProcessor.unDelegateIncrease` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives ResourceProcessor.unDelegateIncrease usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in ResourceProcessor.unDelegateIncrease never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
