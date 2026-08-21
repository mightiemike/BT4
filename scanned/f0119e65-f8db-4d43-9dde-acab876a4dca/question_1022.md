# Q1022: DelegatedResourceAccountIndexCapsule: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.addFromAccount` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker uses DelegatedResourceAccountIndexCapsule.addFromAccount to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in DelegatedResourceAccountIndexCapsule.addFromAccount preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.addFromAccount`
- Entrypoint: broadcast exchange ops via DelegatedResourceAccountIndexCapsule.addFromAccount
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.addFromAccount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses DelegatedResourceAccountIndexCapsule.addFromAccount to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in DelegatedResourceAccountIndexCapsule.addFromAccount preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
