# Q359: secondary-index lock in UpdateAssetActuator.execute

## Question
Can an unprivileged attacker use gRPC createTransaction2 -> broadcastTransaction to make actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java::execute update the primary ledger but leave the secondary tracking state behind, so a later withdraw, cancel, unfreeze, or spend can no longer complete and the user ends up with Permanent lock or misaccounting of transferred value?

## Target
- File/function: actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java::execute
- Entrypoint: gRPC createTransaction2 -> broadcastTransaction
- Attacker controls: owner/to addresses, amount, asset id, permission_id, signatures, and visible/base58/hex encoding
- Exploit idea: Search for flows that add, remove, or rekey orders, delegations, reward entries, permissions, or notes in more than one place and may miss one cleanup path.
- Invariant to test: Whenever sender or issuer balances changes, every corresponding index or lifecycle record in recipient balances, fee burn, or asset accounting must stay synchronized or the asset must remain fully recoverable.
- Expected Immunefi impact: Permanent lock or misaccounting of transferred value
- Fast validation: Exercise create/update/cancel/withdraw sequences via gRPC createTransaction2 -> broadcastTransaction, then assert users can still fully recover funds/resources and no stale index blocks the next legal action.
