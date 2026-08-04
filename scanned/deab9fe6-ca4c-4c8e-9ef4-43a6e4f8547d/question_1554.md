# Q1554: cache-eviction replay in ProposalCapsule.getID

## Question
Can an unprivileged attacker exploit eviction or expiration around /wallet/setaccountid -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/capsule/ProposalCapsule.java::getID forgets enough replay-protection, pending, or receipt state to accept the same logical action again and reach Replayed permission or protected account-control change?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/ProposalCapsule.java::getID
- Entrypoint: /wallet/setaccountid -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, permission_id, threshold, signer weights, operations mask, contract address, and signatures
- Exploit idea: Probe limits, expiration windows, restart behavior, and cache-vs-store disagreements for txs, filters, rewards, or note state.
- Invariant to test: Eviction or restart must never resurrect a completed public action or hide the durable result needed to reject replays.
- Expected Immunefi impact: Replayed permission or protected account-control change
- Fast validation: Push the relevant cache to capacity or restart-equivalent states after one action via /wallet/setaccountid -> sign -> /wallet/broadcasttransaction; assert duplicates still fail.
