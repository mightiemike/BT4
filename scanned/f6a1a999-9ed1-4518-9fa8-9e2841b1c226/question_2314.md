# Q2314: Manager: mempool exhaustion

## Question
Can an unprivileged attacker (broadcast transaction) abuse `Manager.needToMoveAbi` in `framework/src/main/java/org/tron/core/db/Manager.java` — where the attacker floods cheap transactions that Manager.needToMoveAbi admits and holds, exhausting pending memory — to break the invariant that pending admission in Manager.needToMoveAbi is bounded and cost-proportional, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/db/Manager.java` -> `Manager.needToMoveAbi`
- Entrypoint: flood pending via Manager.needToMoveAbi
- Attacker controls: request/transaction/contract inputs to `Manager.needToMoveAbi` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: floods cheap transactions that Manager.needToMoveAbi admits and holds, exhausting pending memory
- Invariant to test: pending admission in Manager.needToMoveAbi is bounded and cost-proportional
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: load-test pending capacity asserting bound
