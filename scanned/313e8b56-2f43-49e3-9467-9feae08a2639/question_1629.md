# Q1629: DelegatedResourceAccountIndexCapsule: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.removeToAccount` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker drives DelegatedResourceAccountIndexCapsule.removeToAccount usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in DelegatedResourceAccountIndexCapsule.removeToAccount never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.removeToAccount`
- Entrypoint: repeated ops via DelegatedResourceAccountIndexCapsule.removeToAccount
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.removeToAccount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives DelegatedResourceAccountIndexCapsule.removeToAccount usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in DelegatedResourceAccountIndexCapsule.removeToAccount never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
