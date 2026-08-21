# Q1739: DelegatedResourceAccountIndexCapsule: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.createReadableString` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker drives DelegatedResourceAccountIndexCapsule.createReadableString usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in DelegatedResourceAccountIndexCapsule.createReadableString never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.createReadableString`
- Entrypoint: repeated ops via DelegatedResourceAccountIndexCapsule.createReadableString
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.createReadableString` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives DelegatedResourceAccountIndexCapsule.createReadableString usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in DelegatedResourceAccountIndexCapsule.createReadableString never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
