# Q1550: write-before-finality replay in ProposalCapsule.hasProcessed

## Question
Can an unprivileged attacker use /wallet/updateBrokerage -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/capsule/ProposalCapsule.java::hasProcessed records replay-protection or settlement state before final outcome is known, then exploit rollback or retry windows to get Replayed permission or protected account-control change?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/ProposalCapsule.java::hasProcessed
- Entrypoint: /wallet/updateBrokerage -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, permission_id, threshold, signer weights, operations mask, contract address, and signatures
- Exploit idea: Look for writes that happen before the final success/failure decision and can later be retried through another public path.
- Invariant to test: Replay-protection and settlement markers must become durable exactly once and only after the final branch is known.
- Expected Immunefi impact: Replayed permission or protected account-control change
- Fast validation: Inject failures after tentative writes via /wallet/updateBrokerage -> sign -> /wallet/broadcasttransaction; assert retries cannot settle again or bypass replay protection.
