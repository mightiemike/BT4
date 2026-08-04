# Q1938: cache-eviction replay in TronDatabase.put

## Question
Can an unprivileged attacker exploit eviction or expiration around /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/db/TronDatabase.java::put forgets enough replay-protection, pending, or receipt state to accept the same logical action again and reach Double application of one logical action?

## Target
- File/function: chainbase/src/main/java/org/tron/core/db/TronDatabase.java::put
- Entrypoint: /wallet/broadcasttransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Probe limits, expiration windows, restart behavior, and cache-vs-store disagreements for txs, filters, rewards, or note state.
- Invariant to test: Eviction or restart must never resurrect a completed public action or hide the durable result needed to reject replays.
- Expected Immunefi impact: Double application of one logical action
- Fast validation: Push the relevant cache to capacity or restart-equivalent states after one action via /wallet/broadcasttransaction; assert duplicates still fail.
