# Q3904: DelegatedResourceAccountIndexStore: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexStore.unDelegateV2` in `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java` — where the attacker drives DelegatedResourceAccountIndexStore.unDelegateV2 usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in DelegatedResourceAccountIndexStore.unDelegateV2 never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java` -> `DelegatedResourceAccountIndexStore.unDelegateV2`
- Entrypoint: repeated ops via DelegatedResourceAccountIndexStore.unDelegateV2
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexStore.unDelegateV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives DelegatedResourceAccountIndexStore.unDelegateV2 usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in DelegatedResourceAccountIndexStore.unDelegateV2 never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
