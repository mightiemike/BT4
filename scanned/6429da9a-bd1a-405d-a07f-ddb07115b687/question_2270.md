# Q2270: write-before-finality replay in DelegatedResourceStore.unLockExpireResource

## Question
Can an unprivileged attacker use /wallet/unfreezebalance -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java::unLockExpireResource records replay-protection or settlement state before final outcome is known, then exploit rollback or retry windows to get Double withdrawal, undelegation, unfreeze, or reward claim?

## Target
- File/function: chainbase/src/main/java/org/tron/core/store/DelegatedResourceStore.java::unLockExpireResource
- Entrypoint: /wallet/unfreezebalance -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner/receiver addresses, resource type, amount, unfreeze or withdraw indexes, permission_id, and signatures
- Exploit idea: Look for writes that happen before the final success/failure decision and can later be retried through another public path.
- Invariant to test: Replay-protection and settlement markers must become durable exactly once and only after the final branch is known.
- Expected Immunefi impact: Double withdrawal, undelegation, unfreeze, or reward claim
- Fast validation: Inject failures after tentative writes via /wallet/unfreezebalance -> sign -> /wallet/broadcasttransaction; assert retries cannot settle again or bypass replay protection.
