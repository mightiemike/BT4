# Q3237: ReceiptCapsule: mempool exhaustion

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ReceiptCapsule.addNetFee` in `chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java` — where the attacker floods cheap transactions that ReceiptCapsule.addNetFee admits and holds, exhausting pending memory — to break the invariant that pending admission in ReceiptCapsule.addNetFee is bounded and cost-proportional, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java` -> `ReceiptCapsule.addNetFee`
- Entrypoint: flood pending via ReceiptCapsule.addNetFee
- Attacker controls: request/transaction/contract inputs to `ReceiptCapsule.addNetFee` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: floods cheap transactions that ReceiptCapsule.addNetFee admits and holds, exhausting pending memory
- Invariant to test: pending admission in ReceiptCapsule.addNetFee is bounded and cost-proportional
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: load-test pending capacity asserting bound
