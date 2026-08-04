# Q1156: double-apply replay in WalletUtil.checkPermissionOperations

## Question
Can an unprivileged attacker repeat, reorder, or rebroadcast the same public flow through /jsonrpc eth_sendRawTransaction so chainbase/src/main/java/org/tron/common/utils/WalletUtil.java::checkPermissionOperations settles one logical public transaction-processing flow more than once, breaks one-time semantics across transaction-processing state and the resulting accounting, receipt, or index state, and results in Double application of one logical action?

## Target
- File/function: chainbase/src/main/java/org/tron/common/utils/WalletUtil.java::checkPermissionOperations
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Probe duplicate tx ids, repeated broadcasts, stale pending state, repeated note or order ids, and re-entry through alternative public APIs.
- Invariant to test: One logical public transaction-processing flow must settle exactly once across transaction-processing state and the resulting accounting, receipt, or index state.
- Expected Immunefi impact: Double application of one logical action
- Fast validation: Submit equivalent payloads twice through /jsonrpc eth_sendRawTransaction and any alternate public path, then assert balances, receipts, orders, rewards, or nullifiers only change once.
