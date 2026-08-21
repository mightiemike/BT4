# Q1987: ReceiptCapsule: mempool exhaustion

## Question
Can an unprivileged attacker (broadcast transaction) abuse `ReceiptCapsule.getReceiptAddress` in `chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java` — where the attacker floods cheap transactions that ReceiptCapsule.getReceiptAddress admits and holds, exhausting pending memory — to break the invariant that pending admission in ReceiptCapsule.getReceiptAddress is bounded and cost-proportional, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java` -> `ReceiptCapsule.getReceiptAddress`
- Entrypoint: flood pending via ReceiptCapsule.getReceiptAddress
- Attacker controls: request/transaction/contract inputs to `ReceiptCapsule.getReceiptAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: floods cheap transactions that ReceiptCapsule.getReceiptAddress admits and holds, exhausting pending memory
- Invariant to test: pending admission in ReceiptCapsule.getReceiptAddress is bounded and cost-proportional
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: load-test pending capacity asserting bound
