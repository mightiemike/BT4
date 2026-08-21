# Q1783: Manager: mempool exhaustion

## Question
Can an unprivileged attacker (broadcast transaction) abuse `Manager.needToUpdateAsset` in `framework/src/main/java/org/tron/core/db/Manager.java` — where the attacker floods cheap transactions that Manager.needToUpdateAsset admits and holds, exhausting pending memory — to break the invariant that pending admission in Manager.needToUpdateAsset is bounded and cost-proportional, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `framework/src/main/java/org/tron/core/db/Manager.java` -> `Manager.needToUpdateAsset`
- Entrypoint: flood pending via Manager.needToUpdateAsset
- Attacker controls: request/transaction/contract inputs to `Manager.needToUpdateAsset` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: floods cheap transactions that Manager.needToUpdateAsset admits and holds, exhausting pending memory
- Invariant to test: pending admission in Manager.needToUpdateAsset is bounded and cost-proportional
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: load-test pending capacity asserting bound
