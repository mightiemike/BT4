# Q1097: DelegatedResourceCapsule: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceCapsule.addFrozenBalanceForBandwidth` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` — where the attacker uses DelegatedResourceCapsule.addFrozenBalanceForBandwidth to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in DelegatedResourceCapsule.addFrozenBalanceForBandwidth preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java` -> `DelegatedResourceCapsule.addFrozenBalanceForBandwidth`
- Entrypoint: broadcast exchange ops via DelegatedResourceCapsule.addFrozenBalanceForBandwidth
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceCapsule.addFrozenBalanceForBandwidth` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses DelegatedResourceCapsule.addFrozenBalanceForBandwidth to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in DelegatedResourceCapsule.addFrozenBalanceForBandwidth preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
