# Q1427: receipt-trace mismatch in DelegatedResourceCapsule.getFrom

## Question
Can an unprivileged attacker reach /wallet/unfreezebalance -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java::getFrom records a receipt, trace, or historical artifact that disagrees with the durable frozen balances, delegated resources, or reward state/withdrawable amounts, vote weight, or receiver entitlements, enabling later logic to act on false settlement state and leading to Double withdrawal, undelegation, unfreeze, or reward claim?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java::getFrom
- Entrypoint: /wallet/unfreezebalance -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner/receiver addresses, resource type, amount, unfreeze or withdraw indexes, permission_id, and signatures
- Exploit idea: Focus on flows where receipts and traces are written separately from the balance or lifecycle state they describe.
- Invariant to test: Historical artifacts must faithfully describe the committed result and must not permit replay, double-claim, or false-spent decisions.
- Expected Immunefi impact: Double withdrawal, undelegation, unfreeze, or reward claim
- Fast validation: Force late failures and ambiguous outcomes via /wallet/unfreezebalance -> sign -> /wallet/broadcasttransaction; compare durable state against every generated receipt, trace, and history record.
