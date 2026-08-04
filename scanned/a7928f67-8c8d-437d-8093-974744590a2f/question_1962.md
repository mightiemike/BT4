# Q1962: cache-eviction replay in AccountStateCallBackUtils.getKey

## Question
Can an unprivileged attacker exploit eviction or expiration around /wallet/createtransaction -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/db/accountstate/AccountStateCallBackUtils.java::getKey forgets enough replay-protection, pending, or receipt state to accept the same logical action again and reach Double settlement of one transfer or asset move?

## Target
- File/function: chainbase/src/main/java/org/tron/core/db/accountstate/AccountStateCallBackUtils.java::getKey
- Entrypoint: /wallet/createtransaction -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner/to addresses, amount, asset id, permission_id, signatures, and visible/base58/hex encoding
- Exploit idea: Probe limits, expiration windows, restart behavior, and cache-vs-store disagreements for txs, filters, rewards, or note state.
- Invariant to test: Eviction or restart must never resurrect a completed public action or hide the durable result needed to reject replays.
- Expected Immunefi impact: Double settlement of one transfer or asset move
- Fast validation: Push the relevant cache to capacity or restart-equivalent states after one action via /wallet/createtransaction -> sign -> /wallet/broadcasttransaction; assert duplicates still fail.
