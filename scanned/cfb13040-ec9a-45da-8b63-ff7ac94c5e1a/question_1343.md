# Q1343: receipt-trace mismatch in AssetIssueCapsule.getData

## Question
Can an unprivileged attacker reach gRPC createTransaction2 -> broadcastTransaction so chainbase/src/main/java/org/tron/core/capsule/AssetIssueCapsule.java::getData records a receipt, trace, or historical artifact that disagrees with the durable sender or issuer balances/recipient balances, fee burn, or asset accounting, enabling later logic to act on false settlement state and leading to Double settlement of one transfer or asset move?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/AssetIssueCapsule.java::getData
- Entrypoint: gRPC createTransaction2 -> broadcastTransaction
- Attacker controls: owner/to addresses, amount, asset id, permission_id, signatures, and visible/base58/hex encoding
- Exploit idea: Focus on flows where receipts and traces are written separately from the balance or lifecycle state they describe.
- Invariant to test: Historical artifacts must faithfully describe the committed result and must not permit replay, double-claim, or false-spent decisions.
- Expected Immunefi impact: Double settlement of one transfer or asset move
- Fast validation: Force late failures and ambiguous outcomes via gRPC createTransaction2 -> broadcastTransaction; compare durable state against every generated receipt, trace, and history record.
