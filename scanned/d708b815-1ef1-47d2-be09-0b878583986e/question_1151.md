# Q1151: secondary-index lock in Commons.putExchangeCapsule

## Question
Can an unprivileged attacker use gRPC broadcastTransaction to make chainbase/src/main/java/org/tron/common/utils/Commons.java::putExchangeCapsule update the primary ledger but leave the secondary tracking state behind, so a later withdraw, cancel, unfreeze, or spend can no longer complete and the user ends up with Permanent lock or stale-state corruption?

## Target
- File/function: chainbase/src/main/java/org/tron/common/utils/Commons.java::putExchangeCapsule
- Entrypoint: gRPC broadcastTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Search for flows that add, remove, or rekey orders, delegations, reward entries, permissions, or notes in more than one place and may miss one cleanup path.
- Invariant to test: Whenever transaction-processing state changes, every corresponding index or lifecycle record in the resulting accounting, receipt, or index state must stay synchronized or the asset must remain fully recoverable.
- Expected Immunefi impact: Permanent lock or stale-state corruption
- Fast validation: Exercise create/update/cancel/withdraw sequences via gRPC broadcastTransaction, then assert users can still fully recover funds/resources and no stale index blocks the next legal action.
