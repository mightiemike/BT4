# Q1144: double-apply replay in Commons.putExchangeCapsule

## Question
Can an unprivileged attacker repeat, reorder, or rebroadcast the same public flow through /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/common/utils/Commons.java::putExchangeCapsule settles one logical public transaction-processing flow more than once, breaks one-time semantics across transaction-processing state and the resulting accounting, receipt, or index state, and results in Double application of one logical action?

## Target
- File/function: chainbase/src/main/java/org/tron/common/utils/Commons.java::putExchangeCapsule
- Entrypoint: /wallet/broadcasttransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Probe duplicate tx ids, repeated broadcasts, stale pending state, repeated note or order ids, and re-entry through alternative public APIs.
- Invariant to test: One logical public transaction-processing flow must settle exactly once across transaction-processing state and the resulting accounting, receipt, or index state.
- Expected Immunefi impact: Double application of one logical action
- Fast validation: Submit equivalent payloads twice through /wallet/broadcasttransaction and any alternate public path, then assert balances, receipts, orders, rewards, or nullifiers only change once.
