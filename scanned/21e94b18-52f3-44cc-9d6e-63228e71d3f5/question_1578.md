# Q1578: cache-eviction replay in SafeExchangeProcessor.exchangeToSupply

## Question
Can an unprivileged attacker exploit eviction or expiration around /wallet/exchangetransaction -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java::exchangeToSupply forgets enough replay-protection, pending, or receipt state to accept the same logical action again and reach Double fill, cancel, or exchange settlement?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java::exchangeToSupply
- Entrypoint: /wallet/exchangetransaction -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, token ids, pair direction, order or cancel ids, quantities, price fields, and signatures
- Exploit idea: Probe limits, expiration windows, restart behavior, and cache-vs-store disagreements for txs, filters, rewards, or note state.
- Invariant to test: Eviction or restart must never resurrect a completed public action or hide the durable result needed to reject replays.
- Expected Immunefi impact: Double fill, cancel, or exchange settlement
- Fast validation: Push the relevant cache to capacity or restart-equivalent states after one action via /wallet/exchangetransaction -> sign -> /wallet/broadcasttransaction; assert duplicates still fail.
