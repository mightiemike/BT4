# Q3829: DelegatedResourceAccountIndexCapsule: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegatedResourceAccountIndexCapsule.removeToAccount` in `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` — where the attacker uses DelegatedResourceAccountIndexCapsule.removeToAccount to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in DelegatedResourceAccountIndexCapsule.removeToAccount preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceAccountIndexCapsule.java` -> `DelegatedResourceAccountIndexCapsule.removeToAccount`
- Entrypoint: broadcast exchange ops via DelegatedResourceAccountIndexCapsule.removeToAccount
- Attacker controls: request/transaction/contract inputs to `DelegatedResourceAccountIndexCapsule.removeToAccount` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses DelegatedResourceAccountIndexCapsule.removeToAccount to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in DelegatedResourceAccountIndexCapsule.removeToAccount preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
