# Q1630: large-iteration underpricing in TransactionResultCapsule.getOrderDetailsList

## Question
Can an unprivileged attacker use gRPC broadcastTransaction so chainbase/src/main/java/org/tron/core/capsule/TransactionResultCapsule.java::getOrderDetailsList performs large iterator walks, pagination scans, or reconstruction passes over pending or recent-transaction state/final settlement, receipts, or replay-protection state below true cost and reaches Materially underpriced public deserialization or broadcast work?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/TransactionResultCapsule.java::getOrderDetailsList
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: raw transaction bytes, signatures, tx ids, permission_id, retry order, duplicate broadcast timing, and visible/base58/hex fields
- Exploit idea: Target account-wide order lists, price indexes, reward histories, note windows, and block/transaction ranges that may require full-store traversal.
- Invariant to test: Publicly reachable iterations over stores and indexes must be bounded and proportionate to the cost or limits exposed to the attacker.
- Expected Immunefi impact: Materially underpriced public deserialization or broadcast work
- Fast validation: Measure scan cost versus returned work for large but valid inputs via gRPC broadcastTransaction; flag any case with attacker-controlled superlinear or large-linear amplification.
