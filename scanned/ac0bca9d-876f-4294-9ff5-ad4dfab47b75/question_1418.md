# Q1418: write-before-finality replay in DelegatedResourceCapsule.createDbKey

## Question
Can an unprivileged attacker use /wallet/withdrawbalance -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java::createDbKey records replay-protection or settlement state before final outcome is known, then exploit rollback or retry windows to get Double withdrawal, undelegation, unfreeze, or reward claim?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/DelegatedResourceCapsule.java::createDbKey
- Entrypoint: /wallet/withdrawbalance -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner/receiver addresses, resource type, amount, unfreeze or withdraw indexes, permission_id, and signatures
- Exploit idea: Look for writes that happen before the final success/failure decision and can later be retried through another public path.
- Invariant to test: Replay-protection and settlement markers must become durable exactly once and only after the final branch is known.
- Expected Immunefi impact: Double withdrawal, undelegation, unfreeze, or reward claim
- Fast validation: Inject failures after tentative writes via /wallet/withdrawbalance -> sign -> /wallet/broadcasttransaction; assert retries cannot settle again or bypass replay protection.
