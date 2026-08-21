# Q1833: DelegatedResourceCapsule: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceCapsule.createDbKeyV2` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` — where the attacker uses DelegatedResourceCapsule.createDbKeyV2 to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in DelegatedResourceCapsule.createDbKeyV2 preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` -> `DelegatedResourceCapsule.createDbKeyV2`
- Entrypoint: broadcast exchange ops via DelegatedResourceCapsule.createDbKeyV2
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceCapsule.createDbKeyV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses DelegatedResourceCapsule.createDbKeyV2 to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in DelegatedResourceCapsule.createDbKeyV2 preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
