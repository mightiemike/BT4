# Q1836: DelegationStore: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `DelegationStore.getWitnessVote` in `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` — where the attacker uses DelegationStore.getWitnessVote to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in DelegationStore.getWitnessVote preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/DelegationStore.java` -> `DelegationStore.getWitnessVote`
- Entrypoint: broadcast exchange ops via DelegationStore.getWitnessVote
- Attacker controls: request/transaction/contract inputs to `DelegationStore.getWitnessVote` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses DelegationStore.getWitnessVote to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in DelegationStore.getWitnessVote preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
