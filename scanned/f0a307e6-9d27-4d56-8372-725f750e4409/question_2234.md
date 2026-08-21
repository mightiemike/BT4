# Q2234: AccountCapsule: mempool exhaustion

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountCapsule.getAddress` in `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` — where the attacker floods cheap transactions that AccountCapsule.getAddress admits and holds, exhausting pending memory — to break the invariant that pending admission in AccountCapsule.getAddress is bounded and cost-proportional, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` -> `AccountCapsule.getAddress`
- Entrypoint: flood pending via AccountCapsule.getAddress
- Attacker controls: request/transaction/contract inputs to `AccountCapsule.getAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: floods cheap transactions that AccountCapsule.getAddress admits and holds, exhausting pending memory
- Invariant to test: pending admission in AccountCapsule.getAddress is bounded and cost-proportional
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: load-test pending capacity asserting bound
