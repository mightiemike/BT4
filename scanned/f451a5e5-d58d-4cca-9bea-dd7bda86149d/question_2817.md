# Q2817: AccountCapsule: mempool exhaustion

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountCapsule.getWitnessPermissionAddress` in `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` — where the attacker floods cheap transactions that AccountCapsule.getWitnessPermissionAddress admits and holds, exhausting pending memory — to break the invariant that pending admission in AccountCapsule.getWitnessPermissionAddress is bounded and cost-proportional, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` -> `AccountCapsule.getWitnessPermissionAddress`
- Entrypoint: flood pending via AccountCapsule.getWitnessPermissionAddress
- Attacker controls: request/transaction/contract inputs to `AccountCapsule.getWitnessPermissionAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: floods cheap transactions that AccountCapsule.getWitnessPermissionAddress admits and holds, exhausting pending memory
- Invariant to test: pending admission in AccountCapsule.getWitnessPermissionAddress is bounded and cost-proportional
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: load-test pending capacity asserting bound
