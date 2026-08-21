# Q2412: DelegationStore: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.buildAccountVoteKey` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker uses DelegationStore.buildAccountVoteKey to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in DelegationStore.buildAccountVoteKey preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.buildAccountVoteKey`
- Entrypoint: broadcast exchange ops via DelegationStore.buildAccountVoteKey
- Attacker controls: request/transaction/contract inputs to `DelegationStore.buildAccountVoteKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses DelegationStore.buildAccountVoteKey to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in DelegationStore.buildAccountVoteKey preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
